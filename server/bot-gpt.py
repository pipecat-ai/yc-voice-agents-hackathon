#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Field & Flower — flower shop voice ordering bot (hackathon starter).

A customer calls in and the bot helps them pick a bouquet and arrange delivery.
All backend calls (catalog, customer lookup, order placement) are mocked so the
starter runs with no external dependencies beyond the AI services.

Pipeline: Gradium STT → OpenAI Responses LLM → Gradium TTS, with direct
function tools registered on the LLM context.

Run the bot using::

    uv run bot-gpt.py
"""

import os
import random
from datetime import date

import aiohttp
from dotenv import load_dotenv
from loguru import logger
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndTaskFrame, FunctionCallResultProperties, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.runner.types import (
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.gradium.stt import GradiumSTTService
from pipecat.services.gradium.tts import GradiumTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.responses.llm import OpenAIResponsesLLMService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.user_turn_strategies import FilterIncompleteUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from mock_backend import BOUQUETS, KNOWN_CUSTOMERS

load_dotenv(override=True)


async def get_call_info(call_sid: str) -> dict:
    """Fetch call information from Twilio REST API using aiohttp.

    Args:
        call_sid: The Twilio call SID

    Returns:
        Dictionary containing call information including from_number, to_number, status, etc.
    """
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]

    if not account_sid or not auth_token:
        logger.warning("Missing Twilio credentials, cannot fetch call info")
        return {}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json"

    try:
        # Use HTTP Basic Auth with aiohttp
        auth = aiohttp.BasicAuth(account_sid, auth_token)

        async with aiohttp.ClientSession() as session:
            async with session.get(url, auth=auth) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Twilio API error ({response.status}): {error_text}")
                    return {}

                data = await response.json()

                call_info = {
                    "from_number": data.get("from"),
                    "to_number": data.get("to"),
                }

                return call_info

    except Exception as e:
        logger.error(f"Error fetching call info from Twilio: {e}")
        return {}


async def run_bot(
    transport: BaseTransport,
    from_number: str | None = None,
    audio_in_sample_rate: int = 16000,
    audio_out_sample_rate: int = 24000,
):
    """Main bot logic.

    Args:
        transport: The transport to use.
        from_number: Caller's phone number (Twilio path only) for known-customer lookup.
        audio_in_sample_rate: Input audio sample rate in Hz. Defaults to 16000 (WebRTC).
        audio_out_sample_rate: Output audio sample rate in Hz. Defaults to 24000 (WebRTC).
    """
    logger.info("Starting bot")

    # Per-call order state. Closed over by the tool functions below so each
    # call gets its own isolated order.
    order: dict = {"items": [], "delivery": None}

    # --- Tools the LLM can call ---------------------------------------------

    async def list_bouquets(
        params: FunctionCallParams,
        occasion: str | None = None,
        specials_only: bool = False,
    ) -> None:
        """List bouquets available today. Optionally filter by occasion or by
        what's currently on special.

        Use this when the caller asks what's available, mentions a specific
        occasion ("it's for my mom's birthday", "for Valentine's Day", "for a
        funeral"), or asks about specials/deals. Sold-out bouquets are
        automatically excluded from results.

        Args:
            occasion: Lowercase occasion to filter by. Common values:
                "birthday", "anniversary", "valentine's day", "mother's day",
                "sympathy", "wedding", "graduation", "thank you", "get well",
                "new baby", "housewarming", "christmas", "easter", "just
                because". Pass the canonical short form ("birthday", not "mom's
                birthday"). Omit to return the full catalog.
            specials_only: If True, only return bouquets currently on special.
        """
        results = []
        for name, info in BOUQUETS.items():
            if not info["in_stock"]:
                continue
            if specials_only and not info.get("on_special", False):
                continue
            if occasion is not None:
                occ = occasion.strip().lower()
                tags = [o.lower() for o in info.get("occasions", [])]
                if not any(occ in tag or tag in occ for tag in tags):
                    continue
            results.append({"name": name, **info})

        if not results and (occasion is not None or specials_only):
            await params.result_callback(
                {
                    "bouquets": [],
                    "note": (
                        "No bouquets match those filters. Tell the caller you don't have "
                        "anything specifically for that, and offer to browse the full "
                        "catalog or try a different angle."
                    ),
                }
            )
            return

        await params.result_callback({"bouquets": results})

    async def check_availability(params: FunctionCallParams, bouquet_name: str) -> None:
        """Check whether a specific bouquet is in stock today.

        Args:
            bouquet_name: The name of the bouquet to check, lowercase.
        """
        item = BOUQUETS.get(bouquet_name.lower())
        if not item:
            await params.result_callback(
                {"available": False, "reason": f"We don't carry a bouquet called '{bouquet_name}'."}
            )
            return
        if not item["in_stock"]:
            await params.result_callback(
                {"available": False, "reason": f"{bouquet_name} is sold out today."}
            )
            return
        await params.result_callback({"available": True, "price": item["price"]})

    async def add_to_order(
        params: FunctionCallParams, bouquet_name: str, quantity: int = 1
    ) -> None:
        """Add a bouquet to the customer's order. Only call this after the
        customer has confirmed they want this bouquet.

        Args:
            bouquet_name: The name of the bouquet to add, lowercase.
            quantity: How many of this bouquet to add. Defaults to 1.
        """
        item = BOUQUETS.get(bouquet_name.lower())
        if not item:
            await params.result_callback(
                {"ok": False, "reason": f"We don't carry a bouquet called '{bouquet_name}'."}
            )
            return
        if not item["in_stock"]:
            await params.result_callback(
                {"ok": False, "reason": f"{bouquet_name} is sold out today."}
            )
            return
        order["items"].append(
            {"bouquet": bouquet_name.lower(), "quantity": quantity, "price": item["price"]}
        )
        await params.result_callback({"ok": True, "items": order["items"]})

    async def get_order_summary(params: FunctionCallParams) -> None:
        """Read back the current order: items, quantities, and running total."""
        total = sum(line["price"] * line["quantity"] for line in order["items"])
        await params.result_callback(
            {"items": order["items"], "total": round(total, 2), "delivery": order["delivery"]}
        )

    async def set_delivery_details(
        params: FunctionCallParams,
        recipient_name: str,
        address: str,
        delivery_date: str,
    ) -> None:
        """Capture delivery details for the order.

        Args:
            recipient_name: Name of the person receiving the flowers.
            address: Delivery street address.
            delivery_date: Requested delivery date, in the customer's own words
                (e.g. "Friday", "May 20th"). No parsing required.
        """
        order["delivery"] = {
            "recipient_name": recipient_name,
            "address": address,
            "delivery_date": delivery_date,
        }
        await params.result_callback({"ok": True, "delivery": order["delivery"]})

    async def place_order(params: FunctionCallParams) -> None:
        """Finalize the order. Only call this after the customer has confirmed
        the items AND delivery details."""
        if not order["items"]:
            await params.result_callback({"ok": False, "reason": "No items in the order yet."})
            return
        if not order["delivery"]:
            await params.result_callback({"ok": False, "reason": "Missing delivery details."})
            return
        total = sum(line["price"] * line["quantity"] for line in order["items"])
        confirmation = f"FLW-{random.randint(100000, 999999)}"
        logger.info(f"Order placed: {confirmation} total=${total:.2f} order={order}")
        await params.result_callback(
            {
                "ok": True,
                "confirmation_number": confirmation,
                "total": round(total, 2),
                "eta": "within 2 business days",
            }
        )

    async def end_call(params: FunctionCallParams) -> None:
        """End the call. Only call this AFTER you have said goodbye to the
        customer in the same turn. The pipeline will flush any queued speech
        and then hang up."""
        logger.info("end_call invoked — pushing EndTaskFrame upstream")
        await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
        # run_llm=False prevents the LLM from generating a follow-up response
        # after this function returns — the goodbye should already be in flight.
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    tool_functions = [
        list_bouquets,
        check_availability,
        add_to_order,
        get_order_summary,
        set_delivery_details,
        place_order,
        end_call,
    ]
    tools = ToolsSchema(standard_tools=tool_functions)

    # --- System instruction (varies based on caller ID) ---------------------

    customer = KNOWN_CUSTOMERS.get(from_number or "")
    if customer:
        caller_context = (
            f"This caller is a returning customer (caller ID matched). On file: "
            f"name {customer['name']}, last order the {customer['last_order']} bouquet. "
            'Greet them generically: "Welcome back to Field & Flower! How can I help '
            'today?" Do not use their name or mention their last order in the greeting; '
            "that comes across as surveilling. Once they say they want flowers, you "
            "can offer their last order as a helpful shortcut, framed as record-keeping: "
            f'"I have you down for the {customer["last_order"]} last time, want that '
            'again or something different?" Always give them the alternative.'
        )
    else:
        caller_context = (
            "You're talking to a new customer. Introduce the shop briefly and ask how you can help."
        )

    system_instruction = (
        "You are a friendly order-taker for Field & Flower, a neighborhood flower shop. "
        "Help callers pick a bouquet and arrange delivery. Use the tools to look up "
        "bouquets, check stock, add items, capture delivery details, and place the order. "
        "Confirm the full order before calling place_order.\n\n"
        "Talk like a real shop clerk on the phone — not a chatbot:\n"
        "- Keep it to 1–2 short sentences per turn. Longer only when listing options or "
        "doing the final order read-back.\n"
        "- Ask ONE thing at a time. Don't ask for name, address, and date in one breath — "
        "ask for the name, wait, then the next.\n"
        '- Skip filler openers like "Absolutely!", "That sounds lovely!", "Perfect!", '
        '"I\'d be happy to" — go straight to the point.\n'
        "- Describe bouquets plainly. \"A dozen red roses with baby's breath, sixty-five "
        'dollars." Not "a classic, romantic bouquet showing love and appreciation."\n'
        "- When listing bouquets, ALWAYS lead with the bouquet's name. Format: "
        '"<Name> — <description>, <price>." For example: "Spring Sunshine — yellow tulips '
        'and daffodils, forty-five dollars." The name is how the caller refers back to it.\n'
        "- When the caller mentions an occasion (birthday, Mother's Day, anniversary, "
        "sympathy, etc.) or asks about specials/deals, pass those as filters to "
        'list_bouquets (occasion="..." or specials_only=True) instead of reading the '
        "full catalog. Don't list 15 bouquets when 3 are relevant.\n"
        "- The catalog has many options — when listing, name at most 4 or 5 at a time. "
        "If the caller doesn't bite, offer to share more.\n"
        "- Don't restate what the customer just said back to them, except in the final "
        "order confirmation.\n"
        "- Use contractions. Fragments are fine.\n\n"
        "Responses are spoken aloud. No bullet points, no emojis. Read prices in words "
        '("forty-five dollars", not "$45.00").\n\n'
        "When the order is placed and the customer has no more requests, or when they say "
        'goodbye: say a short closing line (e.g. "Thanks, have a great day!") AND call '
        "end_call in the same turn. Never call end_call without saying goodbye first.\n\n"
        f"Today is {date.today().strftime('%A, %B %d, %Y')}. Use this when the caller "
        'gives a relative delivery date like "this Friday" or "next Tuesday".\n\n'
        f"Caller context: {caller_context}"
    )

    # Speech-to-Text service
    stt = GradiumSTTService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumSTTService.Settings(
            language=Language.EN,
        ),
    )

    # LLM service
    llm = OpenAIResponsesLLMService(
        api_key=os.environ["OPENAI_API_KEY"],
        settings=OpenAIResponsesLLMService.Settings(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1"),
            system_instruction=system_instruction,
        ),
    )

    # Text-to-Speech service
    tts = GradiumTTSService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumTTSService.Settings(
            voice=os.getenv("GRADIUM_VOICE_ID", "_6Aslh2DxfmnRLmP"),
        ),
    )

    # ToolsSchema describes the tools to the LLM; register_direct_function
    # wires the actual handlers the LLM will invoke. Both are required.
    for fn in tool_functions:
        llm.register_direct_function(fn)

    context = LLMContext(tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=FilterIncompleteUserTurnStrategies(),
        ),
    )

    # Pipeline - assembled from reusable components
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
            audio_in_sample_rate=audio_in_sample_rate,
            audio_out_sample_rate=audio_out_sample_rate,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        # Kick off the conversation
        context.add_message(
            {
                "role": "user",
                "content": "A customer just called. Greet them, 'This is Field & Flower, your local flower shop. How can I help you today?'",
            }
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)

    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Main bot entry point."""

    from_number: str | None = None
    transport_overrides: dict = {}

    # Krisp is available when deployed to Pipecat Cloud
    if os.environ.get("ENV") != "local":
        from pipecat.audio.filters.krisp_viva_filter import KrispVivaFilter

        krisp_filter = KrispVivaFilter()
    else:
        krisp_filter = None

    match runner_args:
        case SmallWebRTCRunnerArguments():
            webrtc_connection: SmallWebRTCConnection = runner_args.webrtc_connection

            transport = SmallWebRTCTransport(
                webrtc_connection=webrtc_connection,
                params=TransportParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                ),
            )
        case WebSocketRunnerArguments():
            # Twilio media streams are 8 kHz μ-law in both directions.
            # This overrides the default sample rates: 16 kHz in / 24 kHz out.
            transport_overrides["audio_in_sample_rate"] = 8000
            transport_overrides["audio_out_sample_rate"] = 8000

            # Parse Twilio websocket and fetch call information
            _, call_data = await parse_telephony_websocket(runner_args.websocket)

            # Fetch call information from Twilio REST API so we can personalize
            # the bot for known customers (see KNOWN_CUSTOMERS).
            call_info = await get_call_info(call_data["call_id"])
            if call_info:
                from_number = call_info.get("from_number")
                logger.info(f"Call from: {from_number} to: {call_info.get('to_number')}")

            serializer = TwilioFrameSerializer(
                stream_sid=call_data["stream_id"],
                call_sid=call_data["call_id"],
                account_sid=os.environ["TWILIO_ACCOUNT_SID"],
                auth_token=os.environ["TWILIO_AUTH_TOKEN"],
            )

            transport = FastAPIWebsocketTransport(
                websocket=runner_args.websocket,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                    add_wav_header=False,
                    serializer=serializer,
                ),
            )
        case _:
            logger.error(f"Unsupported runner arguments type: {type(runner_args)}")
            return

    await run_bot(transport, from_number=from_number, **transport_overrides)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()

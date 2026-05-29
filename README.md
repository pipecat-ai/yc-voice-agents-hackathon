# YC Voice Agents Hackathon

Welcome to the YC Voice Agents Hackathon, hosted by [Cekura](https://cekura.com) and [Daily](https://daily.co), in partnership with [NVIDIA](https://nvidia.com), [AWS](https://aws.amazon.com), and [Twilio](https://twilio.com).

The goal of this event is to learn about building, scaling, evaluating, and continuously improving voice agents.

## Schedule, rules, and prizes

This is a one-day event. Please arrive by 8:30. We'll kick things off at 9:00.

### Schedule

  - 8:00 AM – Doors open & registration
  - 8:30 AM – Breakfast
  - 9:00 AM – Welcome / Hackathon begins
  - 12:00 PM – Lunch
  - 6:00 PM – Submissions due
  - 6:00 - 8:00 PM – Dinner, demos, and conversation
  - 8:00 PM – Judges' presentations
  - 9:00 PM – We all go home

### General guidance

First of all, please respect the YC space. We very much appreciate YC hosting these events. Stay in the designated areas, clean up after meals, and in general be a good guest.

Build something new for this hackathon. Use the tools from Cekura to evaluate and improve the performance of what you build. Use Pipecat as the orchestration framework for your voice agent. We also encourage you to use the open source models from NVIDIA, but it's okay to use any models that work well for your project.

There will be engineers from Cekura, Daily, NVIDIA, AWS, and Twilio available to help you with your project. Don't hesitate to find us.

Judging will start at 6:00. In general, the judges want to showcase interesting projects rather than just pick winners. So don't worry too much about what the judges are looking for in a project. Build something that demonstrates creativity, is interesting on a technical level, or solves a real problem! But do keep in mind that the judges want to see great examples of using Cekura to improve voice agent performance, and using open source models from NVIDIA.


# Tech stack and starting points.

This repo contains two versions of a voice agent built with [Pipecat](https://pipecat.ai).

The demo bot **Field & Flower** is a neighborhood flower shop: callers order a bouquet for delivery while the bot looks up the catalog, captures delivery details, and places the order. All backend calls are mocked, so the starter runs with nothing but AI service keys.

## Version 1 — GPT-4.1

You can start with this before the hackathon, if you want to. Or test GPT-4.1 and Nemotron side-by-side during the hackathon, using Cekura.

This bot only requires a Gradium API key and an OpenAI API key. Sign up for free at [Gradium](https://gradium.ai). We'll provide a code for Gradium credits, during the event.

- **STT:** [Gradium](https://gradium.ai)
- **LLM:** [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses) (GPT-4.1)
- **TTS:** [Gradium](https://gradium.ai)
- **Transports:** SmallWebRTC (local dev) and [Twilio](https://www.twilio.com/en-us) (production telephony)
- **Deploy target:** [Pipecat Cloud](https://pipecat.daily.co)

## Version 2

NVIDIA models hosted on AWS, available during the hackathon. We'll share endpoints for the NVIDIA ASR (STT) and LLM models at the beginning of the day.

- **STT:** [Nemotron Speech Streaming](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b)
- **LLM:** [Nemotron 3 Super 120B](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16)
- **TTS:** [Gradium](https://gradium.ai)
- **Transports:** SmallWebRTC (local dev) and Twilio (production telephony)
- **Deploy target:** [Pipecat Cloud](https://pipecat.daily.co)

## Develop locally

Get the bot running over WebRTC in your browser before you push to the cloud or wire up the phone, for a faster iteration loop.

### Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) package manager
- API keys for [OpenAI](https://platform.openai.com) and [Gradium](https://gradium.ai)

### Setup

1. **Clone and enter the server directory:**

   ```bash
   git clone https://github.com/pipecat-ai/yc-voice-agents-hackathon.git
   cd yc-voice-agents-hackathon/server
   ```

2. **Configure API keys:**

   ```bash
   cp .env.example .env
   # Edit .env and fill in OPENAI_API_KEY, GRADIUM_API_KEY.
   # TWILIO_* keys are only needed when you wire up the phone (next section).
   ```

3. **Install dependencies:**

   ```bash
   uv sync
   ```

4. **Run the bot:**

   ```bash
   # run one or the other of these
   uv run bot-gpt.py
   uv run bot-nemotron.py
   ```

   Open [http://localhost:7860](http://localhost:7860) and click **Connect** to start talking. First launch takes ~20s while Pipecat downloads VAD and turn-detection models.

## Deploy to Pipecat Cloud

Once the bot works locally, deploy to Pipecat Cloud and connect it to a Twilio phone number so anyone can call in.

### Prerequisites

1. [Sign up for Pipecat Cloud](https://pipecat.daily.co/sign-up)
2. Install the [Pipecat CLI](https://github.com/pipecat-ai/pipecat-cli) and log in:

   ```bash
   uv tool install pipecat-ai-cli
   pc cloud auth login
   ```

### Configure Twilio

1. [Add credits / upgrade your Twilio account](https://twil.io/yc-hack)

2. [Buy a phone number](https://help.twilio.com/articles/223135247) with voice capability.

3. Get your Pipecat Cloud organization name:

   ```bash
   pc cloud organizations list
   ```

4. [Create a TwiML Bin](https://www.twilio.com/docs/serverless/twiml-bins/getting-started#create-a-new-twiml-bin) with this configuration:

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <Response>
     <Connect>
       <Stream url="wss://api.pipecat.daily.co/ws/twilio">
         <Parameter name="_pipecatCloudServiceHost"
           value="flower-bot.YOUR_ORG_NAME"/>
       </Stream>
     </Connect>
   </Response>
   ```

   Replace `YOUR_ORG_NAME` with the org name from step 2.

5. [Attach the TwiML Bin](https://www.twilio.com/docs/serverless/twiml-bins/getting-started#wire-your-twiml-bin-up-to-an-incoming-phone-call) to your Twilio number: Go to [your phone numbers](https://console.twilio.com/go?to=/account/__account__/us1/senders-hub/list/phone-numbers/inventory) → select your
number → under **Voice Configuration**, set method to the **TwiML Bin** you created → Save.

6. [Optional] Use [Twilio Dev phone](https://www.twilio.com/docs/labs/dev-phone) for testing.

### Review the deployment configuration

Your deployment details are specified in the `pcc-deploy.toml` file. You can learn more about options in the [docs](https://docs.pipecat.ai/api-reference/cli/cloud/deploy#configuration-file-pcc-deploy-toml).

### Upload secrets

```bash
pc cloud secrets set flower-bot-secrets --file .env
```

This uploads everything from `.env` to Pipecat Cloud's secure storage. The bot reads from there at runtime, so you don't bake keys into the image.

### Deploy

Build and run your bot on Pipecat Cloud:

```bash
pc cloud deploy
```

Learn more about [cloud builds](https://docs.pipecat.ai/pipecat-cloud/guides/cloud-builds).

### Call your bot

Dial the Twilio number you set up. 🌷

## Test your agent with Cekura

[Cekura](https://cekura.com) tests and observes voice agents. For this hackathon, use it to **test the Pipecat bot you build in this repo** — run real conversations against it, score the transcripts, and fix what's failing before you demo.

### Sign up

Create your account at **[dashboard.cekura.ai](https://dashboard.cekura.ai)**. If you're approved for this hackathon, just sign up and your credits will show up automatically. If you don't see them, find someone from the Cekura team, they're on-site.

### Onboarding (or skip it)

On first login you'll land on a short setup flow that helps you create your first agent and test. Feel free to click through it — **or hit _Skip_** and jump straight to the dashboard if you'd rather set things up yourself. Either way takes a minute.

### Recommended: start by testing your agent (via Claude Code)

The fastest path — and what we recommend for the hackathon — is to drive Cekura from **Claude Code** using our MCP server + skills. You stay in your terminal, and Cekura handles agent creation, scenario generation, and running the test.

**1. Install the Cekura skills + MCP** (Claude Code marketplace plugin — bundles the skills, slash commands, and auto-configured MCP server):

```bash
/plugin marketplace add cekura-ai/cekura-skills
/plugin install cekura@cekura-skills
```

Repo: [github.com/cekura-ai/cekura-skills](https://github.com/cekura-ai/cekura-skills) · Full setup + other agents (Cursor, Codex, etc.): **[docs.cekura.ai → Claude Code guide](https://docs.cekura.ai/mcp/claude-code-guide)** and **[Skills](https://docs.cekura.ai/mcp/skills)**.

**2. Run an end-to-end test** of your agent with a single command:

```
/cekura-report
```

This spins up anything from 10–20 evaluators (what Cekura calls test cases), runs scenarios against your Pipecat agent, and gives you back a full report — transcripts, scores, and what failed — so you can iterate fast.

> When connecting your agent, **select `Pipecat` as the provider.** Details: [docs.cekura.ai → Pipecat](https://docs.cekura.ai/documentation/integrations/pipecat/automated).

## Learn more

### Pipecat

- [Pipecat Documentation](https://docs.pipecat.ai/)
- [Pipecat Cloud Deployment](https://docs.pipecat.ai/pipecat-cloud/introduction)
- [Pipecat Examples](https://github.com/pipecat-ai/pipecat-examples)
- [Pipecat Discord](https://discord.gg/pipecat)

### Twilio

- [Twilio Developer Hub](https://www.twilio.com/en-us/developers)
- [Twilio Documentation](https://www.twilio.com/docs)
- [Twilio Dev phone](https://www.twilio.com/docs/labs/dev-phone)

### Cekura

- [Claude Code guide](https://docs.cekura.ai/mcp/claude-code-guide) — MCP + skills setup
- [Cekura skills](https://docs.cekura.ai/mcp/skills) — all slash commands
- [Pipecat integration](https://docs.cekura.ai/documentation/integrations/pipecat/automated)
- [Cekura docs](https://docs.cekura.ai) · [dashboard](https://dashboard.cekura.ai)
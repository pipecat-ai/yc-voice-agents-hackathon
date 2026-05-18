# Voice Agent Hackathon Starter

A telephone-based voice agent built with [Pipecat](https://pipecat.ai). The demo bot **Field & Flower** is a neighborhood flower shop: callers order a bouquet for delivery while the bot looks up the catalog, captures delivery details, and places the order. All backend calls are mocked, so the starter runs with nothing but AI service keys.

## Tech stack

Cascade pipeline running:
- **STT:** [Soniox](https://soniox.com)
- **LLM:** [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses) (GPT-4.1) with direct function tools
- **TTS:** [Gradium](https://gradium.ai)
- **Transports:** SmallWebRTC (local dev) and Twilio (production telephony)
- **Deploy target:** [Pipecat Cloud](https://pipecat.daily.co)

## Try it locally first

Get the bot running over WebRTC in your browser before you wire up the phone, for a faster iteration loop.

### Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) package manager
- API keys for [OpenAI](https://platform.openai.com), [Soniox](https://soniox.com), and [Gradium](https://gradium.ai)

### Setup

1. **Clone and enter the server directory:**

   ```bash
   git clone https://github.com/pipecat-ai/yc-voice-agents-hackathon.git
   cd yc-voice-agents-hackathon/server
   ```

2. **Configure API keys:**

   ```bash
   cp .env.example .env
   # Edit .env and fill in OPENAI_API_KEY, SONIOX_API_KEY, GRADIUM_API_KEY.
   # TWILIO_* keys are only needed when you wire up the phone (next section).
   ```

3. **Install dependencies:**

   ```bash
   uv sync
   ```

4. **Run the bot:**

   ```bash
   uv run bot.py
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

1. [Buy a phone number](https://help.twilio.com/articles/223135247) with voice capability.

2. Get your Pipecat Cloud organization name:

   ```bash
   pc cloud organizations list
   ```

3. Create a [TwiML Bin](https://help.twilio.com/articles/360043489573) with this configuration:

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

4. Attach the TwiML Bin to your Twilio number: **Phone Numbers → Manage → Active numbers → \<your number\> → Voice Configuration → A call comes in → TwiML Bin**, then select the bin you just created. Save.

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

## Learn more

- [Pipecat Documentation](https://docs.pipecat.ai/)
- [Pipecat Cloud Deployment](https://docs.pipecat.ai/pipecat-cloud/introduction)
- [Pipecat Examples](https://github.com/pipecat-ai/pipecat-examples)
- [Pipecat Discord](https://discord.gg/pipecat)

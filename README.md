# Scheduling agent with Conversation Relay and Airtable

## Set up your Twilio Programmable Voice number
To get started, you'll need a Twilio phone number capable of handling incoming voice calls. Check [this tutorial](https://www.twilio.com/docs/usage/tutorials/how-to-use-your-free-trial-account#get-your-first-phone-number) on how to purchase your first number. 

## Set up your backend server
1. Clone this repo 
2. Navigate to the project directory
    ```sh 
    cd conv-relay-scheduling
    ```
3. Install dependencies
    ```sh
    npm install -r requirements.txt
    ```
4. Copy the sample environment file and configure the environment variables
    ```
    cp .env.sample .env
    ```
5. If using the free version of ngrok, run the server on port 8000 and copy the url generated 
    ```
    ngrok http 8000
    ```

Once created, open .env in your code editor. You are required to set the following environment variables for the app to function properly
| Variable Name | Description | 
|-------------------|-----------------------------------------------------|
| `DOMAIN` | The domain of the forwarding URL of your ngrok tunnel initiated above in step 5  |
| `TWILIO_ACCOUNT_SID` | Your Twilio Account SID, which can be found in the Twilio Console.  |
| `TWILIO_AUTH_TOKEN` | Your Twilio Auth Token, which is also found in the Twilio Console. |
| `TWILIO_PHONE_NUMBER` | Twilio phone number to call into. Also used to send sms |
| `OPENAI_API_KEY` | Your OpenAI API Key |
| `AIRTABLE_API_KEY` `AIRTABLE_BASE_ID`| Airtable configuration used for maintaining patient appointments |
| `TWILIO_FLEX_WORKFLOW_SID` | Optional. The Taskrouter Workflow SID, which is automatically provisioned with your Flex account. Used to enqueue inbound call with Flex agents. To find this, in the Twilio Console go to TaskRouter > Workspaces > Flex Task Assignment > Workflows |
| `TTS_VOICE` `TTS_LANGUAGE` | Optional. The voice and language chosen for the agent. Check docs for [available options](https://www.twilio.com/docs/voice/twiml/say/text-speech#available-voices-and-languages)  |
| `TRANSCRIPTION_LANGUAGE` | Optional. The language to use for STT |

## Configure the Voice Webhook
This step tells Twilio where to send the call data when a patient dials your new number. This must point to the endpoint that will initiate the Conversation Relay session.
1. Navigate to **Phone Numbers > Manage > Active numbers**.
2. Click on the number you just purchased.
3. Scroll down to the **Voice** section.
4. Under **A CALL COMES IN**, select **Webhook** and configure it to point to your agent's TwiML-generating endpoint. For this example it is `[your_ngrok_url]/incoming-call`
5. Click **Save configuration**

| Field | Value | 
|-------------------|-----------------------------------------------------|
| **A CALL COMES IN** | Webhook  |
| **Webhook URL** | `[Your Public ngrok url]/incoming-call`  |
| **HTTP Method** | POST  |

## Configure Conversation Relay
Conversation Relay is the control plane that seamlessly connects Twilio Programmable Voice with your LLM agent via a WebSocket connection. It manages the audio stream, handling speech-to-text (STT) and text-to-speech (TTS), relays patient utterances to your agent, while also playing back your LLM responses back to the patient. Twilio requires all Conversation Relay endpoints to use a secure WebSocket connection `wss://`. This means your deployment must be running behind a server with a valid SSL/TLS certificate. The connection is initiated using TwiML (Twilio Markup Language) returned from the webhook endpoint configured above

In order to use Conversation relay, you need to navigate to the **Voice** section, select **General** under **Settings**, and turn on the **Predictive and Generative AI/ML Features Addendum**.

## TwiML for Conversation Relay
The api endpoint for `/incoming-call` is in [src/twilio/call_controller.py](src/twilio/call_controller.py). It returns the following TwiML to hand off the call to Conversation Relay. Some of the attributes are described below but check the [docs](https://www.twilio.com/docs/voice/twiml/connect/conversationrelay#conversationrelay-attributes) for other attributes supported by the `<ConversationRelay>` noun.

```python
connect = Connect(action=config.http_url + "/action")
connect.conversation_relay(
   url=config.ws_url,
   welcome_greeting="Hello, How can I help you today?",
   voice=config.tts_voice,
   language=config.tts_language,
   transcription_language=config.transcription_language
 )
``` 

| Attribute | Description | 
|-------------------|-----------------------------------------------------|
| url | The publicly accessible WebSocket URL (wss://) for your LLM agent server where Twilio will send patient utterances. eg. wss://[domain]/status-callback      |
| welcomeGreeting | The initial message the agent speaks to the patient. Keep this concise to minimize early interruptions. |


## The WebSocket Handshake
When Twilio receives the above TwiML, it initiates a WebSocket connection to your agent server. It sends a [setup message](https://www.twilio.com/docs/voice/conversationrelay/websocket-messages#setup-message) immediately after. Once connected, Conversation Relay begins streaming patient audio, transcribing it, and sending the text utterances to your agent server as JSON payloads via the WebSocket. Check the [documentation](https://www.twilio.com/docs/voice/conversationrelay/websocket-messages#getting-messages-from-twilio) for other messages expected from the Conversation Relay service.

## LLM scheduling agent
The LLM agent is the core logic engine. It receives patient utterances from Conversation Relay, determines the patient's intent, interacts with your scheduling tools (Airtable), and responds with an action (speak, invoke tool, or escalate to human). It also maintains conversation history for the duration of the call. When a message arrives from Conversation Relay, the agent -
1. Extracts the patient's transcribed utterance and appends it to conversation history
2. Streams the LLM response token-by-token back to Conversation Relay so speech synthesis starts immediately
3. If the LLM requests a tool call, executes it server-side (Airtable read/write), feeds the result back to the LLM, and repeats until the LLM produces a final spoken reply — all within a single patient turn
4. Sends the final text tokens to Conversation Relay with `last=true` to signal end of turn
5. If the patient interrupts mid-response, stops streaming and discards buffered tokens

## System Prompt and Tool Definitions
A [clear system prompt](https://github.com/dee-at-twilio/conv-relay-scheduling/blob/main/src/llm/system_prompt.py) is crucial for steering the LLM to act as a reliable scheduling agent.  

**Agent Prompt**
```
You are a courteous, professional patient appointment scheduling assistant for "Atlas Healthcare Clinic." Your job is to help patients schedule, reschedule, or cancel appointments over the phone. You have access to tools to look up patient records, check provider availability, and manage appointments. Always confirm the patient's identity (e.g., full name and DOB) before performing any action. If the patient expresses severe or urgent symptoms (e.g., "in pain," "fever," "needs to be seen today"), immediately stop the standard scheduling process and use the 'escalate_to_human' tool...
```

**Tool Definitions**
The agent needs access to [specific functions](https://github.com/dee-at-twilio/conv-relay-scheduling/blob/main/src/tools/registry.py) to interact with the scheduling system.

The following tools have been made available to the agent:
- Look up patient, doctor, and appointment details
- Schedule a new appointment
- Cancel an existing appointment
- Change/reschedule an appointment
- Send an SMS notification

## Agent WebSocket Messages to Twilio
The agent communicates with Conversation Relay using a standardized JSON payload over the WebSocket. The types of messages the agent can send to Twilio are documented here.

## Airtable integration
Create the following 3 tables (exact names):
- Patients -  Name, Phone, Email
- Providers - Name, Specialty
- Appointments - Provider (link→Providers), Patient (link→Patients), Start Time, End Time, Status (single select), Notes

Rather than calling the SDK directly throughout the app, we wrap it in a [single](https://github.com/dee-at-twilio/conv-relay-scheduling/blob/main/src/airtable/client.py) `AirtableClient` class that handles two concerns: **rate limiting** and **logging**. 

The client exposes three operations. All three follow the same pattern - throttle, log, call, log result, and re-raise exceptions so callers can decide how to handle failures.
- `get_all` 
- `create_record`
- `update_record` 



## Test

Start the server 
```
python3 -m uvicorn src.main:app --reload --port 8000
```
The app should now run on the above port and is ready to accept appointment scheduling calls.

import json
import os
import urllib.parse

import discord
import websockets.client
from dotenv import load_dotenv
from piazza_api.rpc import PiazzaRPC
from websockets.exceptions import WebSocketException
from websockets.typing import Data

from markdownify import MarkdownConverter

load_dotenv()

PIAZZA_EMAIL = os.getenv("PIAZZA_EMAIL")
PIAZZA_PASSWORD = os.getenv("PIAZZA_PASSWORD")
PIAZZA_NID = os.getenv("PIAZZA_CLASS")
PIAZZA_WS = "wss://push.piazza.com/push/sub/ws"

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = os.getenv("FIREHOSE_CHANNEL")

p: PiazzaRPC = PiazzaRPC(PIAZZA_NID)
ws_connected: bool = False

# logging.basicConfig(
#     format="%(asctime)s %(message)s",
#     level=logging.DEBUG,
# )


class PiazzaRTEConverter(MarkdownConverter):
    def convert_blockquote(self, el, text, convert_as_inline):
        return super().convert_blockquote(el, text.strip(), convert_as_inline)

    def convert_pre(self, el, text, convert_as_inline):
        return (
            super()
            .convert_pre(el, text.removeprefix("\n"), convert_as_inline)
            .removeprefix("\n")
        )


def convert_rte(html: str, **options) -> str:
    return PiazzaRTEConverter(**options).convert(html)


def setup_client():
    client = discord.Client()

    # TODO: Additional message "categories"
    # - New top-level posts (feed_item_change with log.length == 0)
    # - Top-level answers to questions (different schema - instructor vs student)

    # TODO: Split message into chunks if necessary
    # TODO: Detect whether the followup is an instructor
    # TODO: convert post IDs to links (@XXX, @XXX_fX)
    # TODO: Handle inline images (in md, rte) properly
    # TODO: Split threads into channels
    # TODO: rich embeds
    #   - Handles both separation of posts and preventing link previews

    # TODO: Pydantic models to more nicely extract?

    @client.event
    async def on_ws_recv(payload: Data):
        msg = json.loads(payload)
        if msg.get("id") == 1:
            return

        if msg.get("text", {}).get("action") == "create" and msg.get("text", {}).get(
            "content", {}
        ).get("type", "") in ["feedback", "followup"]:
            print(msg)
            msg_type = msg["text"]["content"]["type"]
            msg_format = msg["text"]["content"]["config"].get("editor", "plain")
            msg_content: str = msg["text"]["content"]["subject"]

            if msg_format == "rte":
                msg_content = msg_content.replace("\xa0", " ")
                msg_content = convert_rte(msg_content.replace(">\n<", "><"))
            elif msg_format == "md":
                msg_content = msg_content.removeprefix("<md>").removesuffix("</md>")

            channel = client.get_channel(CHANNEL_ID)
            discord_msg = "\n".join(
                (
                    f"Message type: {msg_type}",
                    f"Post ID: {msg['text']['content']['id']}",
                    f"Top-level post: {msg['text']['oid']}",
                    f"Parent: {msg['text']['parent']}",
                    f"User: {msg['text']['content']['uid']}",
                    f"Format: {msg_format}",
                    msg_content,
                )
            )

            channel = client.get_channel(CHANNEL_ID)
            await channel.send(discord_msg)

    # Websocket task that runs in the Discord client's event loop.
    # Keep a persistent connection open, and dispatch received messages to
    # the Client's handler. Piazza is weird and doesn't respond to pings,
    # so we're kind of reading blindly.
    async def launch_ws():
        try:
            p.user_login(email=PIAZZA_EMAIL, password=PIAZZA_PASSWORD)
            response: dict = p.request("user.get_refreshed_tokens")
            uri = PIAZZA_WS + "?" + urllib.parse.urlencode(response["result"])
            await client.get_channel(CHANNEL_ID).send("Websocket connection opened")
            async with websockets.client.connect(
                uri, ping_interval=None, ping_timeout=None
            ) as ws:
                ws_connected = True
                async for message in ws:
                    client.dispatch("ws_recv", message)
            await client.get_channel(CHANNEL_ID).send("Websocket connection closed")
            ws_connected = False
        except (WebSocketException, ConnectionRefusedError) as e:
            print(e)
            await client.get_channel(CHANNEL_ID).send("Websocket connection errored")
            ws_connected = False

    @client.event
    async def on_ready():
        print("Client ready!")
        await launch_ws()

    return client


if __name__ == "__main__":
    client = setup_client()
    # Just a single blocking call :)
    client.run(DISCORD_TOKEN)

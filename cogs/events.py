import discord
from discord.ext import commands, tasks
import os
import datetime
import asyncio
import json
import logging
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
# --- MODIFIED: Import service_account for explicit credential loading ---
from google.cloud import translate_v2 as translate
from google.oauth2 import service_account

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
ANNOUNCED_EVENTS_FILE = 'private/announced_events.json'

# --- Timezone Setup ---
TARGET_TIMEZONE = datetime.timezone(datetime.timedelta(hours=-2))

class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.creds = None
        self.calendar_id = os.environ.get('EVENTS_CALENDAR_ID')
        self.translation_enabled = os.environ.get('TRANSLATE_EVENTS', 'False').lower() in ('true', '1', 't')
        
        # --- MODIFIED: Explicitly load credentials for Translate client ---
        SERVICE_ACCOUNT_FILE = 'private/service_account.json'
        translate_creds = None
        if os.path.exists(SERVICE_ACCOUNT_FILE):
            try:
                translate_creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE)
            except Exception as e:
                logging.error(f"Failed to load service account for translate client: {e}")

        self.translate_client = translate.Client(credentials=translate_creds)
        
        self.language_channels = {}
        
        # Original English channel
        english_channel_id = os.environ.get('EVENTS_CHANNEL_ID_EN')
        if english_channel_id:
            self.language_channels['en'] = int(english_channel_id)
            
        # Chinese (Traditional) channel
        chinese_channel_id = os.environ.get('EVENTS_CHANNEL_ID_ZH_TW')
        if chinese_channel_id:
            self.language_channels['zh-TW'] = int(chinese_channel_id)

        # Spanish channel
        spanish_channel_id = os.environ.get('EVENTS_CHANNEL_ID_ES')
        if spanish_channel_id:
            self.language_channels['es'] = int(spanish_channel_id)

        # Korean channel
        korean_channel_id = os.environ.get('EVENTS_CHANNEL_ID_KO')
        if korean_channel_id:
            self.language_channels['ko'] = int(korean_channel_id)

        self.announced_event_ids = self.load_announced_events()
        
        # --- MOVED: Start task after all attributes are initialized ---
        self.check_for_upcoming_events.start()

    def load_announced_events(self):
        """Loads announced event IDs from a local JSON file."""
        if os.path.exists(ANNOUNCED_EVENTS_FILE):
            try:
                with open(ANNOUNCED_EVENTS_FILE, 'r') as f:
                    return set(json.load(f))
            except json.JSONDecodeError:
                return set()
        return set()

    def save_announced_events(self):
        """Saves current announced event IDs to a local JSON file."""
        with open(ANNOUNCED_EVENTS_FILE, 'w') as f:
            json.dump(list(self.announced_event_ids), f)

    @commands.Cog.listener()
    async def on_ready(self):
        """This event listener is called when the cog is loaded and the bot is ready."""
        await self.bot.wait_until_ready()
        logging.info("Scheduled events task started.")

    def cog_unload(self):
        """Cancel the background task when the cog is unloaded."""
        self.check_for_upcoming_events.cancel()

    async def get_calendar_service(self):
        SERVICE_ACCOUNT_FILE = 'private/service_account.json'
        creds = None
        if os.path.exists(SERVICE_ACCOUNT_FILE):
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        else:
            logging.error(f"Service account key file not found at {SERVICE_ACCOUNT_FILE}")
            if os.path.exists('token.json'):
                self.creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    self.creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                    self.creds = flow.run_local_server(port=0)
                with open('token.json', 'w') as token:
                    token.write(self.creds.to_json())
            creds = self.creds
        # --- MODIFIED: Added cache_discovery=False to the build function ---
        service = build('calendar', 'v3', credentials=creds, cache_discovery=False)
        return service

    async def get_events(self, time_min, time_max):
        """Fetches events from Google Calendar within a specific time window."""
        if not self.calendar_id:
            logging.error("EVENTS_CALENDAR_ID environment variable is not set.")
            return []
        try:
            service = await self.get_calendar_service()
            if not service: return []
            
            events_result = await asyncio.to_thread(
                service.events().list(
                    calendarId=self.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute
            )
            return events_result.get('items', [])
        except Exception as e:
            logging.error(f"Calendar API error", exc_info=True)
            return []

    def translate_text(self, text, target_language):
        """Translates text to the target language."""
        if not text:
            return ""
        try:
            result = self.translate_client.translate(text, target_language=target_language)
            return result['translatedText']
        except Exception as e:
            logging.error(f"Error translating text to {target_language}", exc_info=True)
            return f"Error translating: {text}" # Return original text on error

    @tasks.loop(minutes=1)
    async def check_for_upcoming_events(self):
        """Checks for events starting in the current minute and posts them."""
        if not self.language_channels:
            if not hasattr(self, '_logged_no_channels'):
                logging.error("No event channel IDs are set. Please check your environment variables.")
                self._logged_no_channels = True
            return

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        time_max_utc = now_utc + datetime.timedelta(minutes=1)

        events_to_announce = await self.get_events(
            time_min=now_utc.isoformat(),
            time_max=time_max_utc.isoformat()
        )

        for event in events_to_announce:
            event_id = event['id']
            if event_id in self.announced_event_ids:
                continue

            summary = event.get('summary', 'No Title')
            description = event.get('description')
            start = event['start'].get('dateTime', event['start'].get('date'))

            start_dt_utc = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
            start_dt_target = start_dt_utc.astimezone(TARGET_TIMEZONE)
            start_formatted = start_dt_target.strftime('%A, %b %d at %H:%M') + " (Server Time)"

            for lang, channel_id in self.language_channels.items():
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    logging.error(f"Could not find channel with ID {channel_id} for language {lang}.")
                    continue
                
                if lang != 'en' and not self.translation_enabled:
                    continue
                
                if lang == 'en':
                    translated_summary = summary
                    translated_description = description
                    header = f"**{translated_summary}**"
                    notes_header = "**Notes:**"
                    time_header = "**Time:**"
                else:
                    translated_summary = self.translate_text(summary, lang)
                    translated_description = self.translate_text(description, lang)
                    header = f"**{translated_summary}**"
                    notes_header = f"**{self.translate_text('Notes:', lang)}**"
                    time_header = f"**{self.translate_text('Time:', lang)}**"

                message_parts = [
                    header,
                    "---------------------------------",
                    f"{time_header} {start_formatted}",
                ]

                if translated_description:
                    message_parts.append(f"{notes_header} {translated_description}")
                
                final_message = "\n".join(message_parts)
                await channel.send(final_message)

            self.announced_event_ids.add(event_id)
        
        if events_to_announce:
            self.save_announced_events()

    @commands.hybrid_command(name="upcoming_events", description="Shows the upcoming events for the next 3 days privately.")
    async def upcoming_events(self, ctx: commands.Context):
        """A slash command to get events for the next 3 days privately."""
        await ctx.defer(ephemeral=True)
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            time_max_dt = now + datetime.timedelta(days=3)
            events = await self.get_events(time_min=now.isoformat(), time_max=time_max_dt.isoformat())
            
            if not events:
                await ctx.send("There are no upcoming events in the next 3 days.", ephemeral=True)
                return

            message_parts = ["**The Event Schedule for the Next 3 Days**", "------------------------------------"]
            for event in events:
                summary = event.get('summary', 'No Title')
                start = event['start'].get('dateTime', event['start'].get('date'))
                description = event.get('description')
                if 'T' in start:
                    start_dt_utc = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                    start_dt_target = start_dt_utc.astimezone(TARGET_TIMEZONE)
                    start_formatted = start_dt_target.strftime('%A, %b %d at %H:%M') + " (Server Time)"
                else:
                    start_dt = datetime.datetime.strptime(start, '%Y-%m-%d').date()
                    start_formatted = f"{start_dt.strftime('%A, %b %d')} (All-day)"
                
                event_details = [f"🗓️ **{summary}**", f"**When:** {start_formatted}"]
                if description: event_details.append(f"**Notes:** {description}")
                message_parts.append("\n".join(event_details))

            final_message = "\n\n".join(message_parts) + f"\n\n*Requested by {ctx.author.display_name}*"
            
            if not ctx.interaction:
                try:
                    await ctx.author.send(final_message)
                    await ctx.send("I've sent your schedule to your DMs.", delete_after=10)
                except discord.Forbidden:
                    await ctx.send("I couldn't send you a DM. Please check your privacy settings.")
            else:
                 await ctx.send(final_message, ephemeral=True)

        except Exception as e:
            logging.error(f"Error in upcoming_events command for user {ctx.author.id}", exc_info=True)
            await ctx.send("An error occurred while fetching your schedule. Please try again later.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
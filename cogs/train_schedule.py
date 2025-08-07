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

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
ANNOUNCED_TRAIN_EVENTS_FILE = 'announced_train_events.json'

# --- Timezone Setup ---
TARGET_TIMEZONE = datetime.timezone(datetime.timedelta(hours=-2))

class TrainScheduleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_for_upcoming_trains.start()
        self.creds = None
        self.calendar_id = os.environ.get('TRAIN_CALENDAR_ID')
        
        self.events_channel_ids = []
        channel_id_1 = os.environ.get('TRAIN_EVENTS_CHANNEL_ID')
        channel_id_2 = os.environ.get('TRAIN_EVENTS_CHANNEL_ID_2')

        if channel_id_1:
            try:
                self.events_channel_ids.append(int(channel_id_1))
            except ValueError:
                logging.error(f"Invalid TRAIN_EVENTS_CHANNEL_ID: {channel_id_1}. Must be an integer.")

        if channel_id_2:
            try:
                self.events_channel_ids.append(int(channel_id_2))
            except ValueError:
                logging.error(f"Invalid TRAIN_EVENTS_CHANNEL_ID_2: {channel_id_2}. Must be an integer.")
        
        self.announced_event_ids = self.load_announced_events()

    def load_announced_events(self):
        """Loads announced event IDs from a local JSON file."""
        if os.path.exists(ANNOUNCED_TRAIN_EVENTS_FILE):
            try:
                with open(ANNOUNCED_TRAIN_EVENTS_FILE, 'r') as f:
                    return set(json.load(f))
            except json.JSONDecodeError:
                return set()
        return set()

    def save_announced_events(self):
        """Saves current announced event IDs to a local JSON file."""
        with open(ANNOUNCED_TRAIN_EVENTS_FILE, 'w') as f:
            json.dump(list(self.announced_event_ids), f)

    @commands.Cog.listener()
    async def on_ready(self):
        """This event listener is called when the cog is loaded and the bot is ready."""
        await self.bot.wait_until_ready()
        logging.info("Scheduled train schedule task started.")

    def cog_unload(self):
        """Cancel the background task when the cog is unloaded."""
        self.check_for_upcoming_trains.cancel()
    
    async def get_calendar_service(self):
        from google.oauth2 import service_account
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
        service = build('calendar', 'v3', credentials=creds)
        return service
    
    async def get_train_events(self, time_min, time_max):
        """Fetches upcoming train events from Google Calendar."""
        if not self.calendar_id:
            logging.error("TRAIN_CALENDAR_ID environment variable is not set.")
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
            logging.error(f"Train Calendar API error", exc_info=True)
            return []

    @tasks.loop(minutes=1)
    async def check_for_upcoming_trains(self):
        """Checks for trains departing in the current minute and posts them."""
        if not self.events_channel_ids:
            if not hasattr(self, '_logged_no_channel_ids'):
                logging.error("No train event channel IDs are set. Please check your environment variables.")
                self._logged_no_channel_ids = True
            return

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        time_max_utc = now_utc + datetime.timedelta(minutes=1)

        events_to_announce = await self.get_train_events(
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
            # --- MODIFIED: Use %H:%M for 24-hour format ---
            start_formatted = start_dt_target.strftime('%A, %b %d at %H:%M') + " (UTC-2)"

            # --- MODIFIED: "From" field removed ---
            message_parts = [
                f"**TRAIN DEPARTING NOW: {summary}**",
                "---------------------------------",
                f"**Departure Time:** {start_formatted}",
                f"**Link:** <{event.get('htmlLink', 'N/A')}>"
            ]
            if description:
                message_parts.append(f"**Notes:** {description}")
            
            final_message = "\n".join(message_parts)

            for channel_id in self.events_channel_ids:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    await channel.send(final_message)
                else:
                    logging.error(f"Could not find train schedule channel with ID {channel_id}.")
            
            self.announced_event_ids.add(event_id)

        self.save_announced_events()
    
    @commands.hybrid_command(name="manual_train_trigger", description="Posts train departures in the next 24 hours.")
    @commands.has_permissions(administrator=True)
    async def manual_train_trigger(self, ctx: commands.Context):
        """A manual command to trigger a train schedule post for the next 24 hours."""
        await ctx.defer(ephemeral=True)
        if not self.events_channel_ids:
            await ctx.send(f"Error: No announcement channels are configured.", ephemeral=True)
            return

        try:
            now = datetime.datetime.utcnow()
            time_max_dt = now + datetime.timedelta(days=1)
            time_max_iso = time_max_dt.isoformat() + "Z"
            events = await self.get_train_events(time_min=now.isoformat(), time_max=time_max_iso)
            
            message_parts = ["**Train Departures for the Next 24 Hours**", "---------------------------------"]
            if not events:
                message_parts.append("No upcoming train departures found in the next 24 hours.")
            else:
                for event in events:
                    summary = event.get('summary', 'No Title')
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    description = event.get('description')
                    if 'T' in start:
                        start_dt_utc = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                        start_dt_target = start_dt_utc.astimezone(TARGET_TIMEZONE)
                        # --- MODIFIED: Use %H:%M for 24-hour format ---
                        start_formatted = start_dt_target.strftime('%A, %b %d at %H:%M') + " (UTC-2)"
                    else:
                        start_dt = datetime.datetime.strptime(start, '%Y-%m-%d').date()
                        start_formatted = f"{start_dt.strftime('%A, %b %d')} (All-day)"
                    
                    # --- MODIFIED: "From" field removed ---
                    event_details = [f"🚂 **{summary}**", f"**Departure:** {start_formatted}"]
                    if description: event_details.append(f"**Notes:** {description}")
                    if 'htmlLink' in event: event_details.append(f"[View on Google Calendar](<{event['htmlLink']}>)")
                    message_parts.append("\n".join(event_details))
            
            final_message = "\n\n".join(message_parts)
            
            posted_channels = []
            for channel_id in self.events_channel_ids:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    await channel.send(final_message)
                    posted_channels.append(channel.mention)
            if posted_channels:
                await ctx.send(f"Posted train departures for the next 24 hours to {', '.join(posted_channels)}.", ephemeral=True)
            else:
                await ctx.send("Error: Could not find any of the configured channels.", ephemeral=True)
        except Exception as e:
            logging.error(f"Error in manual_train_trigger command for user {ctx.author.id}", exc_info=True)
            await ctx.send("An error occurred while fetching the train schedule.", ephemeral=True)

    @commands.hybrid_command(name="upcoming_trains", description="Shows upcoming train departures for the next 3 days privately.")
    async def upcoming_trains(self, ctx: commands.Context):
        """A slash command to get train departures for the next 3 days privately."""
        await ctx.defer(ephemeral=True)
        try:
            now = datetime.datetime.utcnow()
            time_max_dt = now + datetime.timedelta(days=3)
            time_max_iso = time_max_dt.isoformat() + "Z"
            events = await self.get_train_events(time_min=now.isoformat(), time_max=time_max_iso)

            if not events:
                await ctx.send("You have no upcoming train departures in the next 3 days.", ephemeral=True)
                return

            message_parts = ["**Your Train Schedule for the Next 3 Days**", "------------------------------------------"]
            for event in events:
                summary = event.get('summary', 'No Title')
                start = event['start'].get('dateTime', event['start'].get('date'))
                description = event.get('description')
                if 'T' in start:
                    start_dt_utc = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                    start_dt_target = start_dt_utc.astimezone(TARGET_TIMEZONE)
                    # --- MODIFIED: Use %H:%M for 24-hour format ---
                    start_formatted = start_dt_target.strftime('%A, %b %d at %H:%M') + " (UTC-2)"
                else:
                    start_dt = datetime.datetime.strptime(start, '%Y-%m-%d').date()
                    start_formatted = f"{start_dt.strftime('%A, %b %d')} (All-day)"
                
                # --- MODIFIED: "From" field removed ---
                event_details = [f"🚂 **{summary}**", f"**Departure:** {start_formatted}"]
                if description: event_details.append(f"**Notes:** {description}")
                if 'htmlLink' in event: event_details.append(f"[View on Google Calendar](<{event['htmlLink']}>)")
                message_parts.append("\n".join(event_details))

            final_message = "\n\n".join(message_parts)
            
            if not ctx.interaction:
                try:
                    await ctx.author.send(final_message)
                    await ctx.send("I've sent your train schedule to your DMs.", delete_after=10)
                except discord.Forbidden:
                    await ctx.send("I couldn't send you a DM. Please check your privacy settings.")
            else:
                 await ctx.send(final_message, ephemeral=True)
        except Exception as e:
            logging.error(f"Error in upcoming_trains command for user {ctx.author.id}", exc_info=True)
            await ctx.send("An error occurred while fetching your train schedule.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(TrainScheduleCog(bot))
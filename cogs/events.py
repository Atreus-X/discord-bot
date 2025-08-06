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
PREVIOUS_EVENTS_FILE = 'previous_events.json'

class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.creds = None
        self.calendar_id = os.environ.get('EVENTS_CALENDAR_ID')
        self.events_channel_id = int(os.environ.get('EVENTS_CHANNEL_ID', '0'))
        self.previous_events = self.load_previous_events()

    def load_previous_events(self):
        """Loads event IDs from a local JSON file."""
        if os.path.exists(PREVIOUS_EVENTS_FILE):
            with open(PREVIOUS_EVENTS_FILE, 'r') as f:
                return json.load(f)
        return {}

    def save_previous_events(self, event_ids):
        """Saves current event IDs to a local JSON file."""
        with open(PREVIOUS_EVENTS_FILE, 'w') as f:
            json.dump(event_ids, f)

    @commands.Cog.listener()
    async def on_ready(self):
        """This event listener is called when the cog is loaded and the bot is ready."""
        # Ensure the bot is connected before starting the task
        await self.bot.wait_until_ready()
        self.post_upcoming_events.start()
        logging.info("Scheduled calendar task started.")

    def cog_unload(self):
        """Cancel the background task when the cog is unloaded."""
        self.post_upcoming_events.cancel()
    
    async def get_calendar_service(self):
        """
        Authenticates with the Google Calendar API using a service account.
        """
        from google.oauth2 import service_account

        SERVICE_ACCOUNT_FILE = 'private/service_account.json' 

        creds = None
        if os.path.exists(SERVICE_ACCOUNT_FILE):
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        else:
            logging.error(f"Service account key file not found at {SERVICE_ACCOUNT_FILE}")
            # Fallback to old method if service account is not found, for local testing.
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
    
    async def get_events(self, max_results=5, time_max=None):
        """Fetches upcoming events from Google Calendar within a specified time window."""
        if not self.calendar_id:
            logging.error("EVENTS_CALENDAR_ID environment variable is not set.")
            return []

        try:
            service = await self.get_calendar_service()
            if not service:
                return []
            
            now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
            
            events_result = await asyncio.to_thread(
                service.events().list(
                    calendarId=self.calendar_id,
                    timeMin=now,
                    maxResults=max_results,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute
            )
            
            return events_result.get('items', [])
        
        except Exception as e:
            logging.error(f"Calendar API error", exc_info=True)
            return []
    
    async def update_events_post(self, channel: discord.TextChannel):
        """Fetches events, updates the post, and handles 'NEW' and 'strikethrough' logic."""
        current_events = await self.get_events()
        current_event_ids = [e['id'] for e in current_events]

        embed = discord.Embed(
            title="Upcoming Google Calendar Events",
            description="Here are the next 5 events:",
            color=discord.Color.red()
        )
        
        target_tz = datetime.timezone(datetime.timedelta(hours=-2))

        for event in current_events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'No Title')
            location = event.get('location', 'No Location')
            
            if 'T' in start:
                start_dt_utc = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                start_dt_target = start_dt_utc.astimezone(target_tz)
                start_formatted = start_dt_target.strftime('%A, %b %d at %I:%M %p') + " (UTC-2)"
            else:
                start_dt = datetime.datetime.strptime(start, '%Y-%m-%d').date()
                start_formatted = f"{start_dt.strftime('%A, %b %d')} (All-day)"

            is_new = event['id'] not in self.previous_events
            
            field_name = f"{summary} {'(NEW)' if is_new else ''}"
            field_value = (
                f"**Start:** {start_formatted}\n"
                f"**Location:** {location}\n"
                f"**Link:** {event.get('htmlLink', 'N/A')}"
            )
            embed.add_field(name=field_name, value=field_value, inline=False)
            
        strikethrough_events = [
            (summary, start_time) for summary, start_time in self.previous_events.items()
            if summary not in [e.get('summary', 'No Title') for e in current_events]
        ]
        
        for summary, start_time in strikethrough_events:
            field_name = f"~~{summary}~~ (Ended)"
            field_value = f"~~**Start:** {start_time}~~"
            embed.add_field(name=field_name, value=field_value, inline=False)

        self.previous_events = {e.get('summary', 'No Title'): e['start'].get('dateTime', e['start'].get('date')) for e in current_events}
        self.save_previous_events(self.previous_events)

        try:
            async for message in channel.history(limit=10):
                if message.author == self.bot.user and message.embeds:
                    await message.delete()
                    break
        except discord.Forbidden:
            logging.warning(f"Bot lacks permission to delete messages in channel {channel.id}.")
        except Exception as e:
            logging.error(f"Error deleting old message in channel {channel.id}", exc_info=True)

        await channel.send(embed=embed)
    
    @tasks.loop(time=[datetime.time(13, 0), datetime.time(1, 0)])
    async def post_upcoming_events(self):
        """A background task to post upcoming events to a designated channel."""
        if self.events_channel_id == 0:
            logging.error("EVENTS_CHANNEL_ID environment variable is not set or is invalid.")
            return

        channel = self.bot.get_channel(self.events_channel_id)
        if not channel:
            logging.error(f"Could not find channel with ID {self.events_channel_id}.")
            return
        
        await self.update_events_post(channel)

    @commands.hybrid_command(name="manual_trigger", description="Posts events occurring in the next 24 hours.")
    @commands.has_permissions(administrator=True)
    async def upcoming_events_command(self, ctx: commands.Context):
        """A manual command to trigger an event post for the next 24 hours."""
        await ctx.defer(ephemeral=True)
        
        channel = self.bot.get_channel(self.events_channel_id)
        
        if not channel:
            await ctx.send(
                f"Error: Could not find channel with ID `{self.events_channel_id}`. Please contact an admin.",
                ephemeral=True
            )
            return

        try:
            # Calculate the time 24 hours from now
            now = datetime.datetime.utcnow()
            time_max_dt = now + datetime.timedelta(days=1)
            time_max_iso = time_max_dt.isoformat() + "Z"

            # Fetch events within the next 24 hours, allowing for a generous number of events.
            events = await self.get_events(max_results=100, time_max=time_max_iso)

            embed = discord.Embed(
                title="Upcoming Events for the Next 24 Hours",
                color=discord.Color.blue()
            )
            
            target_tz = datetime.timezone(datetime.timedelta(hours=-2))

            if not events:
                embed.description = "No upcoming events found in the next 24 hours."
            else:
                for event in events:
                    summary = event.get('summary', 'No Title')
                    start = event['start'].get('dateTime', event['start'].get('date'))

                    if 'T' in start:
                        start_dt_utc = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                        start_dt_target = start_dt_utc.astimezone(target_tz)
                        start_formatted = start_dt_target.strftime('%A, %b %d at %I:%M %p') + " (UTC-2)"
                    else:
                        start_dt = datetime.datetime.strptime(start, '%Y-%m-%d').date()
                        start_formatted = f"{start_dt.strftime('%A, %b %d')} (All-day)"

                    field_value = f"**When:** {start_formatted}"
                    if 'location' in event:
                        field_value += f"\n**Where:** {event['location']}"
                    if 'htmlLink' in event:
                        field_value += f"\n[View on Google Calendar]({event['htmlLink']})"

                    embed.add_field(name=f"🗓️ {summary}", value=field_value, inline=False)
            
            await channel.send(embed=embed)
            await ctx.send(f"Posted events for the next 24 hours to {channel.mention}.", ephemeral=True)

        except Exception as e:
            logging.error(f"Error in manual_trigger command for user {ctx.author.id}", exc_info=True)
            await ctx.send("An error occurred while fetching the events.", ephemeral=True)


    @commands.hybrid_command(name="upcoming_events", description="Shows your upcoming events for the next 3 days privately.")
    async def upcoming_events(self, ctx: commands.Context):
        """A slash command to get events for the next 3 days privately."""
        await ctx.defer(ephemeral=True)

        try:
            now = datetime.datetime.utcnow()
            time_max_dt = now + datetime.timedelta(days=3)
            time_max_iso = time_max_dt.isoformat() + "Z"

            events = await self.get_events(max_results=25, time_max=time_max_iso)

            if not events:
                await ctx.send("You have no upcoming events in the next 3 days.", ephemeral=True)
                return

            embed = discord.Embed(
                title="Your Schedule for the Next 3 Days",
                color=discord.Color.green()
            )

            target_tz = datetime.timezone(datetime.timedelta(hours=-2))

            for event in events:
                summary = event.get('summary', 'No Title')
                start = event['start'].get('dateTime', event['start'].get('date'))

                if 'T' in start:
                    start_dt_utc = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                    start_dt_target = start_dt_utc.astimezone(target_tz)
                    start_formatted = start_dt_target.strftime('%A, %b %d at %I:%M %p') + " (UTC-2)"
                else:
                    start_dt = datetime.datetime.strptime(start, '%Y-%m-%d').date()
                    start_formatted = f"{start_dt.strftime('%A, %b %d')} (All-day)"

                field_value = f"**When:** {start_formatted}"
                if 'location' in event:
                    field_value += f"\n**Where:** {event['location']}"
                if 'htmlLink' in event:
                    field_value += f"\n[View on Google Calendar]({event['htmlLink']})"

                embed.add_field(name=f"🗓️ {summary}", value=field_value, inline=False)

            embed.set_footer(text=f"Requested by {ctx.author.display_name}")
            
            if not ctx.interaction:
                try:
                    await ctx.author.send(embed=embed)
                    await ctx.send("I've sent your schedule to your DMs.", delete_after=10)
                except discord.Forbidden:
                    await ctx.send("I couldn't send you a DM. Please check your privacy settings.")
            else:
                 await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:
            logging.error(f"Error in upcoming_events command for user {ctx.author.id}", exc_info=True)
            await ctx.send("An error occurred while fetching your schedule. Please try again later.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(EventsCog(bot))

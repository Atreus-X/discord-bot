import discord
from discord.ext import commands, tasks
import os
import datetime
import asyncio
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
PREVIOUS_EVENTS_FILE = 'previous_events.json'

class CalendarCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.creds = None
        self.calendar_id = os.environ.get('GOOGLE_CALENDAR_ID')
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
        print("Scheduled calendar task started.")

    def cog_unload(self):
        """Cancel the background task when the cog is unloaded."""
        self.post_upcoming_events.cancel()
    
    async def get_calendar_service(self):
        """
        Authenticates with the Google Calendar API.
        The first time this runs, it will open a browser window for you to
        authorize access. It then saves the token to token.json for future use.
        """
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
        
        service = build('calendar', 'v3', credentials=self.creds)
        return service
    
    async def get_events(self):
        """Fetches the next 5 upcoming events from Google Calendar."""
        if not self.calendar_id:
            print("Error: GOOGLE_CALENDAR_ID environment variable is not set.")
            return []

        try:
            service = await asyncio.to_thread(self.get_calendar_service)
            
            now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
            events_result = service.events().list(
                calendarId=self.calendar_id,
                timeMin=now,
                maxResults=5,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            return events_result.get('items', [])
        
        except Exception as e:
            print(f"Calendar API error: {e}")
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
        
        # Add events to the embed with formatting
        for event in current_events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'No Title')
            location = event.get('location', 'No Location')
            
            start_dt = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
            
            # Check if event is new
            is_new = event['id'] not in self.previous_events
            
            field_name = f"{summary} {'(NEW)' if is_new else ''}"
            field_value = (
                f"**Start:** {start_dt.strftime('%A, %B %d at %I:%M %p UTC')}\n"
                f"**Location:** {location}\n"
                f"**Link:** {event.get('htmlLink', 'N/A')}"
            )
            embed.add_field(name=field_name, value=field_value, inline=False)
            
        # Add old events with strikethrough
        strikethrough_events = [
            (summary, start_time) for summary, start_time in self.previous_events.items()
            if summary not in [e.get('summary', 'No Title') for e in current_events]
        ]
        
        for summary, start_time in strikethrough_events:
            field_name = f"~~{summary}~~ (Ended)"
            field_value = f"~~**Start:** {start_time}~~"
            embed.add_field(name=field_name, value=field_value, inline=False)

        # Update the previous_events dictionary
        self.previous_events = {e.get('summary', 'No Title'): e['start'].get('dateTime', e['start'].get('date')) for e in current_events}
        self.save_previous_events(self.previous_events)

        # Delete the previous bot message if it exists
        try:
            async for message in channel.history(limit=10):
                if message.author == self.bot.user and message.embeds:
                    await message.delete()
                    break
        except discord.Forbidden:
            print(f"Error: Bot lacks permission to delete messages in channel {channel.id}.")
        except Exception as e:
            print(f"Error deleting old message: {e}")

        # Send the new, updated embed
        await channel.send(embed=embed)
    
    # --- Background Task ---
    @tasks.loop(time=[datetime.time(13, 0), datetime.time(1, 0)]) # 8am EST & 8pm EST
    async def post_upcoming_events(self):
        """A background task to post upcoming events to a designated channel."""
        if self.events_channel_id == 0:
            print("Error: EVENTS_CHANNEL_ID environment variable is not set or is invalid.")
            return

        channel = self.bot.get_channel(self.events_channel_id)
        if not channel:
            print(f"Error: Could not find channel with ID {self.events_channel_id}.")
            return
        
        await self.update_events_post(channel)

    # --- Manual Slash Command ---
    @commands.hybrid_command(name="upcoming_events", description="Shows the next 5 upcoming events from the Google Calendar.")
    async def upcoming_events_command(self, interaction: discord.Interaction):
        """A manual command to trigger the event post."""
        await interaction.response.defer()
        
        channel = self.bot.get_channel(self.events_channel_id)
        
        if not channel:
            await interaction.followup.send(
                f"Error: Could not find channel with ID `{self.events_channel_id}`. Please contact an admin.",
                ephemeral=True
            )
            return

        await self.update_events_post(channel)
        await interaction.followup.send(f"Posted upcoming events to {channel.mention}.", ephemeral=True)


# This is the required setup function that main.py will call
async def setup(bot):
    await bot.add_cog(CalendarCog(bot))

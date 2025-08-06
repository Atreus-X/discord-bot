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
PREVIOUS_TRAIN_EVENTS_FILE = 'previous_train_events.json'

class TrainScheduleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.creds = None
        self.calendar_id = os.environ.get('TRAIN_CALENDAR_ID')
        self.events_channel_id = int(os.environ.get('TRAIN_EVENTS_CHANNEL_ID', '0'))
        self.previous_events = self.load_previous_events()

    def load_previous_events(self):
        """Loads event IDs from a local JSON file."""
        if os.path.exists(PREVIOUS_TRAIN_EVENTS_FILE):
            with open(PREVIOUS_TRAIN_EVENTS_FILE, 'r') as f:
                return json.load(f)
        return {}

    def save_previous_events(self, event_ids):
        """Saves current event IDs to a local JSON file."""
        with open(PREVIOUS_TRAIN_EVENTS_FILE, 'w') as f:
            json.dump(event_ids, f)

    @commands.Cog.listener()
    async def on_ready(self):
        """This event listener is called when the cog is loaded and the bot is ready."""
        await self.bot.wait_until_ready()
        self.post_upcoming_trains.start()
        logging.info("Scheduled train schedule task started.")

    def cog_unload(self):
        """Cancel the background task when the cog is unloaded."""
        self.post_upcoming_trains.cancel()
    
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
    
    async def get_train_events(self, max_results=5, time_max=None):
        """Fetches upcoming train events from Google Calendar."""
        if not self.calendar_id:
            logging.error("TRAIN_CALENDAR_ID environment variable is not set.")
            return []

        try:
            service = await self.get_calendar_service()
            if not service:
                return []
            
            now = datetime.datetime.utcnow().isoformat() + 'Z'
            
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
            logging.error(f"Train Calendar API error", exc_info=True)
            return []
    
    async def update_train_events_post(self, channel: discord.TextChannel):
        """Fetches train events and updates the post."""
        current_events = await self.get_train_events()

        embed = discord.Embed(
            title="Upcoming Train Departures",
            description="Here are the next 5 train departures:",
            color=discord.Color.dark_gold()
        )
        
        target_tz = datetime.timezone(datetime.timedelta(hours=-2))

        for event in current_events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'No Title')
            location = event.get('location', 'No Location')
            description = event.get('description')
            
            if 'T' in start:
                start_dt_utc = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                start_dt_target = start_dt_utc.astimezone(target_tz)
                start_formatted = start_dt_target.strftime('%A, %b %d at %I:%M %p') + " (UTC-2)"
            else:
                start_dt = datetime.datetime.strptime(start, '%Y-%m-%d').date()
                start_formatted = f"{start_dt.strftime('%A, %b %d')} (All-day)"

            field_name = f"🚂 {summary}"
            field_value = (
                f"**Departure:** {start_formatted}\n"
                f"**From:** {location}\n"
                f"**Link:** {event.get('htmlLink', 'N/A')}"
            )
            if description:
                field_value += f"\n**Notes:** {description}"
            embed.add_field(name=field_name, value=field_value, inline=False)
            
        try:
            async for message in channel.history(limit=10):
                if message.author == self.bot.user and message.embeds and "Train Departures" in message.embeds[0].title:
                    await message.delete()
                    break
        except discord.Forbidden:
            logging.warning(f"Bot lacks permission to delete messages in channel {channel.id}.")
        except Exception as e:
            logging.error(f"Error deleting old train schedule message in channel {channel.id}", exc_info=True)

        await channel.send(embed=embed)
    
    @tasks.loop(time=[datetime.time(13, 0), datetime.time(1, 0)])
    async def post_upcoming_trains(self):
        """A background task to post upcoming train schedules."""
        if self.events_channel_id == 0:
            logging.error("TRAIN_EVENTS_CHANNEL_ID environment variable is not set or is invalid.")
            return

        channel = self.bot.get_channel(self.events_channel_id)
        if not channel:
            logging.error(f"Could not find train schedule channel with ID {self.events_channel_id}.")
            return
        
        await self.update_train_events_post(channel)

    @commands.hybrid_command(name="manual_train_trigger", description="Posts train departures in the next 24 hours.")
    @commands.has_permissions(administrator=True)
    async def manual_train_trigger(self, ctx: commands.Context):
        """A manual command to trigger a train schedule post for the next 24 hours."""
        await ctx.defer(ephemeral=True)
        
        channel = self.bot.get_channel(self.events_channel_id)
        
        if not channel:
            await ctx.send(
                f"Error: Could not find channel with ID `{self.events_channel_id}`. Please contact an admin.",
                ephemeral=True
            )
            return

        try:
            now = datetime.datetime.utcnow()
            time_max_dt = now + datetime.timedelta(days=1)
            time_max_iso = time_max_dt.isoformat() + "Z"

            events = await self.get_train_events(max_results=100, time_max=time_max_iso)

            embed = discord.Embed(
                title="Train Departures for the Next 24 Hours",
                color=discord.Color.dark_gold()
            )
            
            target_tz = datetime.timezone(datetime.timedelta(hours=-2))

            if not events:
                embed.description = "No upcoming train departures found in the next 24 hours."
            else:
                for event in events:
                    summary = event.get('summary', 'No Title')
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    description = event.get('description')

                    if 'T' in start:
                        start_dt_utc = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                        start_dt_target = start_dt_utc.astimezone(target_tz)
                        start_formatted = start_dt_target.strftime('%A, %b %d at %I:%M %p') + " (UTC-2)"
                    else:
                        start_dt = datetime.datetime.strptime(start, '%Y-%m-%d').date()
                        start_formatted = f"{start_dt.strftime('%A, %b %d')} (All-day)"

                    field_value = f"**Departure:** {start_formatted}"
                    if 'location' in event:
                        field_value += f"\n**From:** {event['location']}"
                    if description:
                        field_value += f"\n**Notes:** {description}"
                    if 'htmlLink' in event:
                        field_value += f"\n[View on Google Calendar]({event['htmlLink']})"

                    embed.add_field(name=f"🚂 {summary}", value=field_value, inline=False)
            
            await channel.send(embed=embed)
            await ctx.send(f"Posted train departures for the next 24 hours to {channel.mention}.", ephemeral=True)

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

            events = await self.get_train_events(max_results=25, time_max=time_max_iso)

            if not events:
                await ctx.send("You have no upcoming train departures in the next 3 days.", ephemeral=True)
                return

            embed = discord.Embed(
                title="Your Train Schedule for the Next 3 Days",
                color=discord.Color.dark_blue()
            )

            target_tz = datetime.timezone(datetime.timedelta(hours=-2))

            for event in events:
                summary = event.get('summary', 'No Title')
                start = event['start'].get('dateTime', event['start'].get('date'))
                description = event.get('description')

                if 'T' in start:
                    start_dt_utc = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                    start_dt_target = start_dt_utc.astimezone(target_tz)
                    start_formatted = start_dt_target.strftime('%A, %b %d at %I:%M %p') + " (UTC-2)"
                else:
                    start_dt = datetime.datetime.strptime(start, '%Y-%m-%d').date()
                    start_formatted = f"{start_dt.strftime('%A, %b %d')} (All-day)"

                field_value = f"**Departure:** {start_formatted}"
                if 'location' in event:
                    field_value += f"\n**From:** {event['location']}"
                if description:
                    field_value += f"\n**Notes:** {description}"
                if 'htmlLink' in event:
                    field_value += f"\n[View on Google Calendar]({event['htmlLink']})"

                embed.add_field(name=f"🚂 {summary}", value=field_value, inline=False)

            embed.set_footer(text=f"Requested by {ctx.author.display_name}")
            
            if not ctx.interaction:
                try:
                    await ctx.author.send(embed=embed)
                    await ctx.send("I've sent your train schedule to your DMs.", delete_after=10)
                except discord.Forbidden:
                    await ctx.send("I couldn't send you a DM. Please check your privacy settings.")
            else:
                 await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:
            logging.error(f"Error in upcoming_trains command for user {ctx.author.id}", exc_info=True)
            await ctx.send("An error occurred while fetching your train schedule.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(TrainScheduleCog(bot))

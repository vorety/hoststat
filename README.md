HostStat Bot

Telegram bot for Linux system monitoring and management.

Features

· System information (CPU, memory, disks)
· Network monitoring and speed tests
· File manager to browse and download files
· Terminal for command execution
· Process management
· User and command management
· Security features and logging

Requirements

· Python 3.8+
· Linux system
· Telegram bot token from @BotFather
· Your Telegram user ID from @userinfobot

Installation

1. Clone the repository:
   git clone https://github.com/vorety/hoststat.git
   cd hoststat
3. Create virtual environment:
   python3 -m venv venv
5. Activate virtual environment:
   source venv/bin/activate
7. Install dependencies:
   pip install -r requirements.txt

Configuration

1. Get bot token from @BotFather on Telegram
2. Get your user ID from @userinfobot on Telegram
3. Edit bot.py file:
   · Replace "YOUR_BOT_TOKEN" with your actual token
   · Replace ADMINS_ID with your Telegram user ID

Running the Bot

Activate virtual environment if not active:
source venv/bin/activate

Run the bot:
python bot.py

Bot Commands

/start - Show main menu
/ping [host] - Ping a host
/back - Return to main menu
/admin - Access admin panel

Main Menu Options

· System Information: CPU, memory, uptime
· Disk & Memory: Storage usage
· Network & Internet: Stats, speed test, ping
· File Manager: Browse and manage files
· Processes: Running processes
· Terminal: Execute commands
· Utilities: System tools

Admin Panel

· Statistics: Usage metrics
· User Management: Block/unblock users
· Command Management: Control allowed commands
· Logs: View activity history
· Restart: Restart the bot

Security Notes

1. Keep your bot token secret
2. Only add trusted users to AUTHORIZED_IDS
3. Use strong sudo passwords
4. Review logs regularly for suspicious activity

Troubleshooting

If bot doesn't start:

· Check Python version (python3 --version)
· Verify token is correct
· Check internet connection

If sudo commands fail:

· Verify user has sudo privileges
· Check sudo password

If file access fails:

· Check file permissions
· Verify file exists
· Check file size (max 50MB)

Files

bot.py - Main bot file
requirements.txt - Python dependencies
bot_admin.db - Database (created automatically)

License

MIT License

Support

For issues, check:

1. Bot token and user ID are correct
2. Virtual environment is activated
3. All dependencies are installed
4. System has internet access to Telegram API

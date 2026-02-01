import os
import subprocess
import json
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InputFile
from aiogram.client.default import DefaultBotProperties
import psutil
import logging
import platform
import socket
import speedtest
import getpass
import sqlite3
from pathlib import Path

BOT_TOKEN = "YOUR_BOT_TOKEN"
AUTHORIZED_IDS = {ADMINS_ID}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
user_states = {}
sudo_attempts = {}
sudo_passwords = {}

DB_PATH = "bot_admin.db"

def init_db():
    """Initialize database tables if they don't exist"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS allowed_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT NOT NULL,
            allowed INTEGER DEFAULT 1,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blocked_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            reason TEXT,
            blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def log_action(user_id, action, details=""):
    """Log user actions to database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bot_logs (user_id, action, details) VALUES (?, ?, ?)",
            (user_id, action, details)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Logging error: {e}")

def is_authorized(user_id):
    """Check if user is authorized and not blocked"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM blocked_users WHERE user_id = ?", (user_id,))
        blocked = cursor.fetchone() is not None
        conn.close()
        
        return user_id in AUTHORIZED_IDS and not blocked
    except Exception as e:
        logging.error(f"Authorization check error: {e}")
        return False

def is_command_allowed(command):
    """Check if command is allowed in database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT allowed FROM allowed_commands WHERE command = ?", (command,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return result[0] == 1
        return True
    except Exception as e:
        logging.error(f"Command check error: {e}")
        return True

def seconds_to_human(seconds):
    """Convert seconds to human readable format"""
    days = seconds // (24 * 3600)
    hours = (seconds % (24 * 3600)) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    result = []
    if days > 0:
        result.append(f"{days}d")
    if hours > 0:
        result.append(f"{hours}h")
    if minutes > 0:
        result.append(f"{minutes}m")
    result.append(f"{secs}s")
    return " ".join(result)

def get_network_info():
    """Get network interface statistics"""
    info = []
    for name, stats in psutil.net_io_counters(pernic=True).items():
        if name != 'lo':
            info.append(f"<b>{name}</b>:")
            info.append(f"  📥 {stats.bytes_recv // 1024**2:.1f} MB")
            info.append(f"  📤 {stats.bytes_sent // 1024**2:.1f} MB")
            info.append(f"  🔄 Packets: {stats.packets_recv}/{stats.packets_sent}")
    return "\n".join(info) if info else "No network data"

async def ping_host(host="8.8.8.8"):
    """Ping a host and return result"""
    try:
        result = await asyncio.create_subprocess_exec(
            "ping", "-c", "3", host,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await result.communicate()
        return stdout.decode('utf-8', errors='ignore')[:1000]
    except:
        return "Ping error"

async def speed_test():
    """Run internet speed test"""
    try:
        st = speedtest.Speedtest()
        st.get_best_server()
        download = st.download() / 1024 / 1024
        upload = st.upload() / 1024 / 1024
        return f"📥 Download: {download:.1f} Mbps\n📤 Upload: {upload:.1f} Mbps"
    except:
        return "Speed test error"

async def execute_with_sudo(command, password):
    """Execute command with sudo privileges"""
    try:
        command_lower = command.lower()
        
        # Auto-add -y for apt install commands
        if "apt install" in command_lower or "apt-get install" in command_lower:
            if "-y" not in command and "--yes" not in command and "--assume-yes" not in command:
                if "apt install" in command_lower:
                    command = command.replace("apt install", "apt install -y")
                elif "apt-get install" in command_lower:
                    command = command.replace("apt-get install", "apt-get install -y")
        
        # Auto-add -y for apt upgrade commands
        elif any(cmd in command_lower for cmd in ["apt update", "apt upgrade", "apt dist-upgrade", "apt-get update", "apt-get upgrade"]):
            if "-y" not in command and "--yes" not in command and "--assume-yes" not in command:
                if "apt update" in command_lower or "apt-get update" in command_lower:
                    pass
                else:
                    command = command + " -y"
        
        # Remove sudo prefix if present
        if command.startswith("sudo "):
            command_to_run = command[5:]
        else:
            command_to_run = command
        
        # Execute with sudo
        process = await asyncio.create_subprocess_shell(
            f'echo "{password}" | sudo -S bash -c "{command_to_run}"',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
        output = stdout.decode('utf-8', errors='ignore')
        error = stderr.decode('utf-8', errors='ignore')
        
        # Check for authentication errors
        if "sudo: аутентификация не удалась" in error or "sudo: authentication failure" in error:
            return None, "❌ Incorrect sudo password"
        
        if "[sudo] password for" in error:
            return None, "❌ Incorrect sudo password"
        
        return output, error
    except asyncio.TimeoutError:
        return None, "⏱️ Command timeout (300 seconds)"
    except Exception as e:
        return None, f"❌ Error: {str(e)}"

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    """Main menu - start command"""
    if not is_authorized(message.from_user.id):
        return
    
    log_action(message.from_user.id, "start_command")
    
    if message.from_user.id in user_states:
        user_states.pop(message.from_user.id)
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📊 System Info", callback_data="sysinfo")],
        [types.InlineKeyboardButton(text="💾 Disk & Memory", callback_data="diskinfo")],
        [types.InlineKeyboardButton(text="🌐 Network & Internet", callback_data="networkinfo")],
        [types.InlineKeyboardButton(text="📁 File Manager", callback_data="files")],
        [types.InlineKeyboardButton(text="⚡ Processes", callback_data="processes")],
        [types.InlineKeyboardButton(text="🖥️ Terminal", callback_data="terminal")],
        [types.InlineKeyboardButton(text="🔧 Utilities", callback_data="utils")]
    ])
    await message.answer("🖥️ <b>Host Control Panel</b>\nSelect section:", reply_markup=keyboard)

@dp.message(Command("ping"))
async def ping_command(message: types.Message):
    """Ping a host"""
    if not is_authorized(message.from_user.id):
        return
    
    log_action(message.from_user.id, "ping_command")
    
    args = message.text.split()
    host = "8.8.8.8"
    if len(args) > 1:
        host = args[1]
    
    await message.answer(f"🏓 <b>Ping Test</b>\nHost: {host}\n\n<i>Running...</i>")
    result = await ping_host(host)
    await message.answer(f"<b>🏓 Ping Test ({host})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{result}</pre>")

@dp.message(Command("back"))
async def back_command(message: types.Message):
    """Go back to main menu"""
    if not is_authorized(message.from_user.id):
        return
    
    log_action(message.from_user.id, "back_command")
    
    user_id = message.from_user.id
    if user_id in user_states:
        user_states.pop(user_id)
    
    await start_handler(message)

@dp.message(Command("admin"))
async def admin_command(message: types.Message):
    """Admin panel"""
    if not is_authorized(message.from_user.id):
        return
    
    log_action(message.from_user.id, "admin_command")
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📊 Bot Statistics", callback_data="admin_stats")],
        [types.InlineKeyboardButton(text="👥 User Management", callback_data="admin_users")],
        [types.InlineKeyboardButton(text="⚡ Command Management", callback_data="admin_commands")],
        [types.InlineKeyboardButton(text="📝 Bot Logs", callback_data="admin_logs")],
        [types.InlineKeyboardButton(text="🔄 Restart Bot", callback_data="admin_restart")],
        [types.InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])
    await message.answer("⚙️ <b>Bot Admin Panel</b>\nSelect action:", reply_markup=keyboard)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: types.CallbackQuery):
    """Show bot statistics"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM bot_logs")
        total_logs = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM bot_logs")
        unique_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM allowed_commands")
        total_commands = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM blocked_users")
        blocked_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT action, COUNT(*) FROM bot_logs GROUP BY action ORDER BY COUNT(*) DESC LIMIT 5")
        top_actions = cursor.fetchall()
        
        conn.close()
        
        stats_text = f"""
<b>📊 Bot Statistics</b>
━━━━━━━━━━━━━━━━━━━━━━
<b>General Info:</b>
├─ Total logs: {total_logs}
├─ Unique users: {unique_users}
├─ Commands in DB: {total_commands}
└─ Blocked users: {blocked_users}

<b>Top 5 Actions:</b>
"""
        for action, count in top_actions:
            stats_text += f"├─ {action}: {count}\n"
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Refresh", callback_data="admin_stats")],
            [types.InlineKeyboardButton(text="🔙 Back", callback_data="admin_menu")]
        ])
        
        await callback.message.edit_text(stats_text, reply_markup=keyboard)
        
    except Exception as e:
        logging.error(f"Error in admin_stats_handler: {e}")
        await callback.message.edit_text(f"❌ Error getting stats: {str(e)}", reply_markup=back_to_admin_button())

@dp.callback_query(F.data == "admin_users")
async def admin_users_handler(callback: types.CallbackQuery):
    """User management menu"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="👁️ View Blocked", callback_data="admin_view_blocked")],
            [types.InlineKeyboardButton(text="🚫 Block User", callback_data="admin_block_user")],
            [types.InlineKeyboardButton(text="✅ Unblock User", callback_data="admin_unblock_user")],
            [types.InlineKeyboardButton(text="🔙 Back", callback_data="admin_menu")]
        ])
        await callback.message.edit_text("<b>👥 User Management</b>\nSelect action:", reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_users_handler: {e}")
        await callback.answer(f"❌ Error: {str(e)}")

@dp.callback_query(F.data == "admin_view_blocked")
async def admin_view_blocked_handler(callback: types.CallbackQuery):
    """View blocked users"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, reason, blocked_at FROM blocked_users ORDER BY blocked_at DESC")
        blocked_users = cursor.fetchall()
        conn.close()
        
        if not blocked_users:
            text = "📭 <b>No blocked users</b>"
        else:
            text = "<b>🚫 Blocked Users:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
            for user_id, reason, blocked_at in blocked_users:
                text += f"├─ ID: {user_id}\n"
                if reason:
                    text += f"│  Reason: {reason}\n"
                text += f"└─ Date: {blocked_at}\n\n"
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Refresh", callback_data="admin_view_blocked")],
            [types.InlineKeyboardButton(text="🔙 Back", callback_data="admin_users")]
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_view_blocked_handler: {e}")
        await callback.message.edit_text(f"❌ Error: {str(e)}", reply_markup=back_to_admin_button())

@dp.callback_query(F.data == "admin_block_user")
async def admin_block_user_handler(callback: types.CallbackQuery):
    """Block a user"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        await callback.message.edit_text(
            "🚫 <b>Block User</b>\n\n"
            "Enter user ID and reason (space separated):\n"
            "<i>Example: 123456789 Spam</i>\n\n"
            "Or press ❌ Cancel to return"
        )
        user_states[callback.from_user.id] = {"mode": "wait_block_user"}
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="admin_users")]
        ])
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_block_user_handler: {e}")
        await callback.answer(f"❌ Error: {str(e)}")

@dp.callback_query(F.data == "admin_unblock_user")
async def admin_unblock_user_handler(callback: types.CallbackQuery):
    """Unblock a user"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        await callback.message.edit_text(
            "✅ <b>Unblock User</b>\n\n"
            "Enter user ID to unblock:\n\n"
            "Or press ❌ Cancel to return"
        )
        user_states[callback.from_user.id] = {"mode": "wait_unblock_user"}
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="admin_users")]
        ])
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_unblock_user_handler: {e}")
        await callback.answer(f"❌ Error: {str(e)}")

@dp.callback_query(F.data == "admin_commands")
async def admin_commands_handler(callback: types.CallbackQuery):
    """Command management menu"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT command, allowed FROM allowed_commands ORDER BY command")
        commands = cursor.fetchall()
        conn.close()
        
        if not commands:
            text = "📭 <b>No commands in database</b>"
        else:
            text = "<b>⚡ Command Management:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
            for command, allowed in commands:
                status = "✅" if allowed == 1 else "❌"
                text += f"{status} <code>{command}</code>\n"
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="➕ Add Command", callback_data="admin_add_command")],
            [types.InlineKeyboardButton(text="🚫 Disable Command", callback_data="admin_disable_command")],
            [types.InlineKeyboardButton(text="✅ Enable Command", callback_data="admin_enable_command")],
            [types.InlineKeyboardButton(text="🗑️ Remove Command", callback_data="admin_remove_command")],
            [types.InlineKeyboardButton(text="🔙 Back", callback_data="admin_menu")]
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_commands_handler: {e}")
        await callback.message.edit_text(f"❌ Error: {str(e)}", reply_markup=back_to_admin_button())

@dp.callback_query(F.data == "admin_add_command")
async def admin_add_command_handler(callback: types.CallbackQuery):
    """Add a command to database"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        await callback.message.edit_text(
            "➕ <b>Add Command</b>\n\n"
            "Enter command to add to database:\n"
            "<i>Example: rm -rf / or sudo reboot</i>\n\n"
            "Or press ❌ Cancel to return"
        )
        user_states[callback.from_user.id] = {"mode": "wait_add_command"}
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="admin_commands")]
        ])
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_add_command_handler: {e}")
        await callback.answer(f"❌ Error: {str(e)}")

@dp.callback_query(F.data == "admin_disable_command")
async def admin_disable_command_handler(callback: types.CallbackQuery):
    """Disable a command"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        await callback.message.edit_text(
            "🚫 <b>Disable Command</b>\n\n"
            "Enter command to disable:\n\n"
            "Or press ❌ Cancel to return"
        )
        user_states[callback.from_user.id] = {"mode": "wait_disable_command"}
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="admin_commands")]
        ])
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_disable_command_handler: {e}")
        await callback.answer(f"❌ Error: {str(e)}")

@dp.callback_query(F.data == "admin_enable_command")
async def admin_enable_command_handler(callback: types.CallbackQuery):
    """Enable a command"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        await callback.message.edit_text(
            "✅ <b>Enable Command</b>\n\n"
            "Enter command to enable:\n\n"
            "Or press ❌ Cancel to return"
        )
        user_states[callback.from_user.id] = {"mode": "wait_enable_command"}
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="admin_commands")]
        ])
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_enable_command_handler: {e}")
        await callback.answer(f"❌ Error: {str(e)}")

@dp.callback_query(F.data == "admin_remove_command")
async def admin_remove_command_handler(callback: types.CallbackQuery):
    """Remove a command from database"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        await callback.message.edit_text(
            "🗑️ <b>Remove Command</b>\n\n"
            "Enter command to remove from database:\n\n"
            "Or press ❌ Cancel to return"
        )
        user_states[callback.from_user.id] = {"mode": "wait_remove_command"}
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="admin_commands")]
        ])
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_remove_command_handler: {e}")
        await callback.answer(f"❌ Error: {str(e)}")

@dp.callback_query(F.data == "admin_logs")
async def admin_logs_handler(callback: types.CallbackQuery):
    """View bot logs"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, action, details, timestamp 
            FROM bot_logs 
            ORDER BY timestamp DESC 
            LIMIT 20
        """)
        logs = cursor.fetchall()
        conn.close()
        
        if not logs:
            text = "📭 <b>No logs</b>"
        else:
            text = "<b>📝 Last 20 logs:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
            for user_id, action, details, timestamp in logs:
                text += f"├─ ID: {user_id}\n"
                text += f"│  Action: {action}\n"
                if details:
                    text += f"│  Details: {details[:50]}...\n"
                text += f"└─ Time: {timestamp}\n\n"
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📥 Download All Logs", callback_data="admin_download_logs")],
            [types.InlineKeyboardButton(text="🗑️ Clear Logs", callback_data="admin_clear_logs")],
            [types.InlineKeyboardButton(text="🔙 Back", callback_data="admin_menu")]
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_logs_handler: {e}")
        await callback.message.edit_text(f"❌ Error: {str(e)}", reply_markup=back_to_admin_button())

@dp.callback_query(F.data == "admin_download_logs")
async def admin_download_logs_handler(callback: types.CallbackQuery):
    """Download all logs as file"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bot_logs ORDER BY timestamp DESC")
        logs = cursor.fetchall()
        conn.close()
        
        log_file = "/tmp/bot_logs.txt"
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("Bot logs:\n")
            f.write("="*50 + "\n")
            for log in logs:
                f.write(f"{log}\n")
        
        await bot.send_document(callback.from_user.id, FSInputFile(log_file), caption="📝 Bot logs")
        os.remove(log_file)
        await callback.answer("✅ Logs sent")
    except Exception as e:
        logging.error(f"Error in admin_download_logs_handler: {e}")
        await callback.answer(f"❌ Error: {str(e)}")

@dp.callback_query(F.data == "admin_clear_logs")
async def admin_clear_logs_handler(callback: types.CallbackQuery):
    """Clear all logs confirmation"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Yes, clear", callback_data="admin_confirm_clear_logs")],
            [types.InlineKeyboardButton(text="❌ No", callback_data="admin_logs")]
        ])
        await callback.message.edit_text("⚠️ <b>Clear all logs?</b>\nThis action cannot be undone.", reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_clear_logs_handler: {e}")
        await callback.answer(f"❌ Error: {str(e)}")

@dp.callback_query(F.data == "admin_confirm_clear_logs")
async def admin_confirm_clear_logs_handler(callback: types.CallbackQuery):
    """Clear all logs"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_logs")
        conn.commit()
        conn.close()
        
        await callback.message.edit_text("✅ <b>Logs cleared</b>", reply_markup=back_to_admin_button())
    except Exception as e:
        logging.error(f"Error in admin_confirm_clear_logs_handler: {e}")
        await callback.message.edit_text(f"❌ Error: {str(e)}", reply_markup=back_to_admin_button())

@dp.callback_query(F.data == "admin_restart")
async def admin_restart_handler(callback: types.CallbackQuery):
    """Restart bot confirmation"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Restart", callback_data="admin_confirm_restart")],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="admin_menu")]
        ])
        await callback.message.edit_text("⚠️ <b>Restart bot?</b>\nBot will be stopped and must be started manually.", reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in admin_restart_handler: {e}")
        await callback.answer(f"❌ Error: {str(e)}")

@dp.callback_query(F.data == "admin_confirm_restart")
async def admin_confirm_restart_handler(callback: types.CallbackQuery):
    """Restart bot"""
    if not is_authorized(callback.from_user.id):
        return
    
    try:
        await callback.message.edit_text("🔄 <b>Restarting bot...</b>\nStopping in 3 seconds.")
        await asyncio.sleep(3)
        import sys
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        logging.error(f"Error in admin_confirm_restart_handler: {e}")
        await callback.message.edit_text(f"❌ Restart error: {str(e)}")

@dp.callback_query(F.data == "sysinfo")
async def sysinfo_handler(callback: types.CallbackQuery):
    """Show system information"""
    if not is_authorized(callback.from_user.id):
        return
    
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_freq = psutil.cpu_freq()
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    boot_time = psutil.boot_time()
    uptime = datetime.now() - datetime.fromtimestamp(boot_time)
    
    info = f"""
<b>🖥️ System Information</b>
━━━━━━━━━━━━━━━━━━━━━━
<b>CPU:</b>
├─ Load: {cpu_percent}%
├─ Frequency: {cpu_freq.current:.0f} MHz
└─ Cores: {psutil.cpu_count()} ({psutil.cpu_count(logical=False)} physical)

<b>Memory:</b>
├─ RAM: {memory.percent}% ({memory.used // 1024**2} MB / {memory.total // 1024**2} MB)
└─ Swap: {swap.percent}% ({swap.used // 1024**2} MB / {swap.total // 1024**2} MB)

<b>System:</b>
├─ Uptime: {seconds_to_human(int(uptime.total_seconds()))}
├─ Load average: {', '.join([f'{x:.2f}' for x in psutil.getloadavg()])}
└─ Platform: {platform.system()} {platform.release()}
"""
    await callback.message.edit_text(info, reply_markup=back_to_main_button())

@dp.callback_query(F.data == "diskinfo")
async def diskinfo_handler(callback: types.CallbackQuery):
    """Show disk information"""
    if not is_authorized(callback.from_user.id):
        return
    
    disks_info = ["<b>💾 Disk & Memory</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            used_gb = usage.used // 1024**3
            total_gb = usage.total // 1024**3
            
            bar_length = 10
            filled = int(bar_length * usage.percent / 100)
            bar = '█' * filled + '░' * (bar_length - filled)
            
            disks_info.append(f"<b>{part.device}</b> ({part.fstype})")
            disks_info.append(f"├─ {part.mountpoint}")
            disks_info.append(f"├─ {bar} {usage.percent}%")
            disks_info.append(f"└─ {used_gb} GB / {total_gb} GB")
            disks_info.append("")
        except:
            continue
    
    await callback.message.edit_text("\n".join(disks_info), reply_markup=back_to_main_button())

@dp.callback_query(F.data == "networkinfo")
async def networkinfo_handler(callback: types.CallbackQuery):
    """Network information menu"""
    if not is_authorized(callback.from_user.id):
        return
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📊 Network Stats", callback_data="net_stats")],
        [types.InlineKeyboardButton(text="📡 Speed Test", callback_data="net_speed")],
        [types.InlineKeyboardButton(text="🏓 Ping Test", callback_data="net_ping")],
        [types.InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])
    await callback.message.edit_text("<b>🌐 Network & Internet</b>\nSelect action:", reply_markup=keyboard)

@dp.callback_query(F.data == "net_stats")
async def net_stats_handler(callback: types.CallbackQuery):
    """Show network statistics"""
    if not is_authorized(callback.from_user.id):
        return
    
    network_info = get_network_info()
    
    try:
        hostname = socket.gethostname()
        ip_local = socket.gethostbyname(hostname)
        
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_public = s.getsockname()[0]
        s.close()
    except:
        ip_local = "Unavailable"
        ip_public = "Unavailable"
    
    info = f"""
<b>📊 Network Statistics</b>
━━━━━━━━━━━━━━━━━━━━━━
<b>Addresses:</b>
├─ Host: {hostname}
├─ Local IP: {ip_local}
└─ Public IP: {ip_public}

<b>Interfaces:</b>
{network_info}
"""
    await callback.message.edit_text(info, reply_markup=back_to_main_button())

@dp.callback_query(F.data == "net_speed")
async def net_speed_handler(callback: types.CallbackQuery):
    """Run speed test"""
    if not is_authorized(callback.from_user.id):
        return
    
    await callback.message.edit_text("⏳ <i>Running speed test...</i>")
    result = await speed_test()
    await callback.message.edit_text(f"<b>📡 Speed Test</b>\n━━━━━━━━━━━━━━━━━━━━━━\n{result}", reply_markup=back_to_main_button())

@dp.callback_query(F.data == "net_ping")
async def net_ping_handler(callback: types.CallbackQuery):
    """Run ping test"""
    if not is_authorized(callback.from_user.id):
        return
    
    await callback.message.edit_text("⏳ <i>Ping test running...</i>")
    result = await ping_host()
    await callback.message.edit_text(f"<b>🏓 Ping Test (8.8.8.8)</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{result}</pre>", reply_markup=back_to_main_button())

@dp.callback_query(F.data == "processes")
async def processes_handler(callback: types.CallbackQuery):
    """Show active processes"""
    if not is_authorized(callback.from_user.id):
        return
    
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
        try:
            info = proc.info
            if info['cpu_percent'] > 0 or info['memory_percent'] > 0.1:
                processes.append(info)
        except:
            continue
    
    processes.sort(key=lambda x: x['memory_percent'] or 0, reverse=True)
    
    text_lines = ["<b>⚡ Active Processes</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    for proc in processes[:15]:
        text_lines.append(f"<b>PID {proc['pid']}</b> | {proc['name'][:20]}")
        text_lines.append(f"├─ CPU: {proc['cpu_percent']}%")
        text_lines.append(f"└─ MEM: {proc['memory_percent']:.1f}%\n")
    
    await callback.message.edit_text("\n".join(text_lines), reply_markup=back_to_main_button())

@dp.callback_query(F.data == "files")
async def files_handler(callback: types.CallbackQuery):
    """File manager menu"""
    if not is_authorized(callback.from_user.id):
        return
    
    user_states[callback.from_user.id] = {"path": os.path.expanduser("~")}
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🏠 Home Folder", callback_data="nav_home")],
        [types.InlineKeyboardButton(text="📂 Root /", callback_data="nav_root")],
        [types.InlineKeyboardButton(text="📊 Logs", callback_data="nav_logs")],
        [types.InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])
    await callback.message.edit_text("<b>📁 File Manager</b>\nSelect starting directory:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("nav_"))
async def navigate_handler(callback: types.CallbackQuery):
    """Navigate to predefined directories"""
    if not is_authorized(callback.from_user.id):
        return
    
    paths = {
        "nav_home": os.path.expanduser("~"),
        "nav_root": "/",
        "nav_logs": "/var/log"
    }
    
    if callback.data in paths:
        user_states[callback.from_user.id] = {"path": paths[callback.data]}
    
    await list_directory(callback)

async def list_directory(callback: types.CallbackQuery, path=None):
    """List directory contents"""
    if not is_authorized(callback.from_user.id):
        return
    
    user_state = user_states.get(callback.from_user.id, {"path": os.path.expanduser("~")})
    current_path = path or user_state["path"]
    
    try:
        items = os.listdir(current_path)
    except Exception as e:
        await callback.message.edit_text(f"❌ Error: {e}", reply_markup=back_to_main_button())
        return
    
    dirs = []
    files = []
    
    for item in sorted(items)[:50]:
        full_path = os.path.join(current_path, item)
        try:
            if os.path.isdir(full_path):
                dirs.append(item)
            else:
                size = os.path.getsize(full_path)
                files.append((item, size))
        except:
            continue
    
    keyboard_buttons = []
    
    if current_path != "/":
        parent_dir = os.path.dirname(current_path)
        keyboard_buttons.append([
            types.InlineKeyboardButton(text="⬆️ Up", callback_data=f"dir_{parent_dir}")
        ])
    
    for dir_name in dirs[:20]:
        keyboard_buttons.append([
            types.InlineKeyboardButton(text=f"📁 {dir_name}", callback_data=f"dir_{os.path.join(current_path, dir_name)}")
        ])
    
    for file_name, size in files[:20]:
        size_str = f"{size // 1024}KB" if size < 1024*1024 else f"{size // 1024**2}MB"
        keyboard_buttons.append([
            types.InlineKeyboardButton(text=f"📄 {file_name} ({size_str})", callback_data=f"file_{os.path.join(current_path, file_name)}")
        ])
    
    keyboard_buttons.append([types.InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")])
    
    text = f"<b>📁 {current_path}</b>\n"
    text += f"📁 Folders: {len(dirs)} | 📄 Files: {len(files)}\n━━━━━━━━━━━━━━━━━━━━━━"
    
    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons))

@dp.callback_query(F.data.startswith("dir_"))
async def change_directory(callback: types.CallbackQuery):
    """Change directory in file manager"""
    if not is_authorized(callback.from_user.id):
        return
    
    new_path = callback.data[4:]
    user_states[callback.from_user.id] = {"path": new_path}
    await list_directory(callback, new_path)

@dp.callback_query(F.data.startswith("file_"))
async def handle_file(callback: types.CallbackQuery):
    """Handle file selection in file manager"""
    if not is_authorized(callback.from_user.id):
        return
    
    file_path = callback.data[5:]
    
    if not os.path.exists(file_path):
        await callback.answer("❌ File not found")
        return
    
    file_size = os.path.getsize(file_path)
    
    if file_size > 50 * 1024 * 1024:
        await callback.answer("❌ File too large (>50MB)")
        return
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬇️ Download", callback_data=f"download_{file_path}")],
        [types.InlineKeyboardButton(text="👁️ View", callback_data=f"view_{file_path}")],
        [types.InlineKeyboardButton(text="🔙 Back", callback_data=f"dir_{os.path.dirname(file_path)}")]
    ])
    
    size_str = f"{file_size // 1024}KB" if file_size < 1024*1024 else f"{file_size // 1024**2}MB"
    await callback.message.edit_text(f"<b>📄 {os.path.basename(file_path)}</b>\nSize: {size_str}\nPath: {file_path}", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("download_"))
async def download_file(callback: types.CallbackQuery):
    """Download a file"""
    if not is_authorized(callback.from_user.id):
        return
    
    file_path = callback.data[9:]
    
    try:
        await bot.send_document(callback.from_user.id, InputFile(file_path))
        await callback.answer("✅ File sent")
    except Exception as e:
        await callback.answer(f"❌ Error: {e}")

@dp.callback_query(F.data.startswith("view_"))
async def view_file(callback: types.CallbackQuery):
    """View file content"""
    if not is_authorized(callback.from_user.id):
        return
    
    file_path = callback.data[5:]
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read(4000)
        
        await callback.message.answer(f"<pre>{content}</pre>")
        await callback.answer()
    except Exception as e:
        await callback.answer(f"❌ Error: {e}")

@dp.callback_query(F.data == "terminal")
async def terminal_handler(callback: types.CallbackQuery):
    """Terminal menu"""
    if not is_authorized(callback.from_user.id):
        return
    
    user_states[callback.from_user.id] = {"mode": "terminal"}
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📊 System Status", callback_data="cmd_status")],
        [types.InlineKeyboardButton(text="📁 List Files", callback_data="cmd_ls")],
        [types.InlineKeyboardButton(text="🔧 Custom Command", callback_data="cmd_custom")],
        [types.InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(
        "<b>🖥️ Terminal</b>\nSelect command or enter your own:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("cmd_"))
async def execute_predefined(callback: types.CallbackQuery):
    """Execute predefined commands"""
    if not is_authorized(callback.from_user.id):
        return
    
    commands = {
        "cmd_status": "top -bn1 | head -20",
        "cmd_ls": "ls -la ~",
        "cmd_custom": "custom"
    }
    
    cmd = commands.get(callback.data)
    
    if cmd == "custom":
        await callback.message.edit_text(
            "💬 <b>Enter command to execute:</b>\n\n"
            "<i>Examples:\n"
            "• ls -la\n"
            "• df -h\n"
            "• ps aux | head -20\n"
            "• sudo apt update</i>\n\n"
            "For sudo commands, password will be requested separately"
        )
        user_states[callback.from_user.id] = {"mode": "wait_command"}
        return
    
    await execute_command(callback.message, cmd)

async def execute_command(message: types.Message, cmd_text=None):
    """Execute a shell command"""
    if not is_authorized(message.from_user.id):
        return
    
    cmd = cmd_text or message.text
    
    log_action(message.from_user.id, "execute_command", cmd)
    
    if not is_command_allowed(cmd):
        await message.answer(f"🚫 Command <code>{cmd}</code> blocked by admin")
        return
    
    # Check for dangerous commands
    dangerous_commands = ["rm -rf /", "dd if=", ":(){:|:&};:", "mkfs", "fdisk", "shutdown"]
    for dangerous in dangerous_commands:
        if dangerous in cmd.lower():
            await message.answer(f"🚫 Command blocked for security: {dangerous}")
            return
    
    user_id = message.from_user.id
    
    # Check sudo attempts
    if user_id in sudo_attempts:
        attempts, timestamp = sudo_attempts[user_id]
        if datetime.now().timestamp() - timestamp < 300:
            if attempts >= 3:
                await message.answer("🚫 Too many failed sudo attempts. Try in 5 minutes.")
                return
    
    # Handle sudo commands
    if cmd.startswith("sudo "):
        if user_id in sudo_passwords:
            password = sudo_passwords[user_id]["password"]
            timestamp = sudo_passwords[user_id]["timestamp"]
            
            if datetime.now().timestamp() - timestamp < 300:
                await message.answer("⏳ <i>Using saved sudo password...</i>")
                output, error = await execute_with_sudo(cmd, password)
                
                if output is None:
                    del sudo_passwords[user_id]
                    await message.answer(error)
                    return
                else:
                    await send_command_output(message, cmd, output, error)
                    return
            else:
                del sudo_passwords[user_id]
        
        await message.answer("🔐 <b>Sudo password required</b>\nEnter password to execute command:")
        user_states[user_id] = {"mode": "wait_sudo_password", "sudo_command": cmd}
        return
    
    # Execute regular command
    try:
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        output = stdout.decode('utf-8', errors='ignore') or stderr.decode('utf-8', errors='ignore')
        
        await send_command_output(message, cmd, output, "")
        
    except asyncio.TimeoutError:
        await message.answer("⏱️ Command timeout (30 sec)")
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}")
    
    await start_handler(message)

async def send_command_output(message, cmd, output, error):
    """Send command output to user"""
    combined_output = output + (error if error else "")
    
    if not combined_output.strip():
        combined_output = "✅ Command executed, output empty"
    
    # If output too large, send as file
    if len(combined_output) > 4000:
        with open("/tmp/bot_output.txt", "w") as f:
            f.write(combined_output)
        await message.answer_document(FSInputFile("/tmp/bot_output.txt"), caption=f"Command output: {cmd[:100]}")
        os.remove("/tmp/bot_output.txt")
    else:
        await message.answer(f"<b>Command:</b> <code>{cmd}</code>\n<b>Output:</b>\n<pre>{combined_output[:3500]}</pre>")

@dp.message()
async def handle_messages(message: types.Message):
    """Handle all text messages from users"""
    if not is_authorized(message.from_user.id):
        return
    
    user_id = message.from_user.id
    user_state = user_states.get(user_id, {})
    
    if user_state.get("mode") == "wait_command":
        user_states[user_id] = {}
        await execute_command(message)
    
    elif user_state.get("mode") == "wait_sudo_password":
        sudo_command = user_state.get("sudo_command", "")
        password = message.text
        
        await message.answer("⏳ <i>Executing sudo command...</i>")
        
        output, error = await execute_with_sudo(sudo_command, password)
        
        if output is None:
            # Wrong password
            if user_id not in sudo_attempts:
                sudo_attempts[user_id] = [1, datetime.now().timestamp()]
            else:
                attempts, _ = sudo_attempts[user_id]
                sudo_attempts[user_id] = [attempts + 1, datetime.now().timestamp()]
            
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
            ])
            await message.answer(f"{error}", reply_markup=keyboard)
        else:
            # Correct password - save for 5 minutes
            sudo_passwords[user_id] = {
                "password": password,
                "timestamp": datetime.now().timestamp()
            }
            
            if user_id in sudo_attempts:
                del sudo_attempts[user_id]
            
            await send_command_output(message, sudo_command, output, error)
        
        user_states[user_id] = {}
        await start_handler(message)
    
    # Admin state handlers
    elif user_state.get("mode") == "wait_block_user":
        parts = message.text.split(maxsplit=1)
        if len(parts) >= 1:
            try:
                target_user_id = int(parts[0])
                reason = parts[1] if len(parts) > 1 else "Administrator"
                
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO blocked_users (user_id, reason) VALUES (?, ?)",
                    (target_user_id, reason)
                )
                conn.commit()
                conn.close()
                
                log_action(user_id, "block_user", f"target: {target_user_id}, reason: {reason}")
                await message.answer(f"✅ User {target_user_id} blocked. Reason: {reason}")
            except ValueError:
                await message.answer("❌ Invalid user ID format")
            except Exception as e:
                await message.answer(f"❌ Error: {str(e)}")
        user_states[user_id] = {}
        await admin_command(message)
    
    elif user_state.get("mode") == "wait_unblock_user":
        try:
            target_user_id = int(message.text)
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM blocked_users WHERE user_id = ?", (target_user_id,))
            conn.commit()
            conn.close()
            
            log_action(user_id, "unblock_user", f"target: {target_user_id}")
            await message.answer(f"✅ User {target_user_id} unblocked")
        except ValueError:
            await message.answer("❌ Invalid user ID format")
        except Exception as e:
            await message.answer(f"❌ Error: {str(e)}")
        user_states[user_id] = {}
        await admin_command(message)
    
    elif user_state.get("mode") == "wait_add_command":
        command = message.text.strip()
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO allowed_commands (command, allowed) VALUES (?, ?)",
                (command, 1)
            )
            conn.commit()
            conn.close()
            
            log_action(user_id, "add_command", command)
            await message.answer(f"✅ Command <code>{command}</code> added")
        except Exception as e:
            await message.answer(f"❌ Error adding command: {str(e)}")
        user_states[user_id] = {}
        await admin_command(message)
    
    elif user_state.get("mode") == "wait_disable_command":
        command = message.text.strip()
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("UPDATE allowed_commands SET allowed = 0 WHERE command = ?", (command,))
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO allowed_commands (command, allowed) VALUES (?, ?)",
                    (command, 0)
                )
            conn.commit()
            conn.close()
            
            log_action(user_id, "disable_command", command)
            await message.answer(f"🚫 Command <code>{command}</code> disabled")
        except Exception as e:
            await message.answer(f"❌ Error disabling command: {str(e)}")
        user_states[user_id] = {}
        await admin_command(message)
    
    elif user_state.get("mode") == "wait_enable_command":
        command = message.text.strip()
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("UPDATE allowed_commands SET allowed = 1 WHERE command = ?", (command,))
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO allowed_commands (command, allowed) VALUES (?, ?)",
                    (command, 1)
                )
            conn.commit()
            conn.close()
            
            log_action(user_id, "enable_command", command)
            await message.answer(f"✅ Command <code>{command}</code> enabled")
        except Exception as e:
            await message.answer(f"❌ Error enabling command: {str(e)}")
        user_states[user_id] = {}
        await admin_command(message)
    
    elif user_state.get("mode") == "wait_remove_command":
        command = message.text.strip()
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM allowed_commands WHERE command = ?", (command,))
            conn.commit()
            conn.close()
            
            log_action(user_id, "remove_command", command)
            await message.answer(f"🗑️ Command <code>{command}</code> removed from database")
        except Exception as e:
            await message.answer(f"❌ Error removing command: {str(e)}")
        user_states[user_id] = {}
        await admin_command(message)
    
    else:
        await start_handler(message)

@dp.callback_query(F.data == "utils")
async def utils_handler(callback: types.CallbackQuery):
    """Utilities menu"""
    if not is_authorized(callback.from_user.id):
        return
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔄 Reboot", callback_data="util_reboot")],
        [types.InlineKeyboardButton(text="⏸️ Shutdown", callback_data="util_shutdown")],
        [types.InlineKeyboardButton(text="🗑️ Clear Cache", callback_data="util_clearcache")],
        [types.InlineKeyboardButton(text="📊 Full Report", callback_data="util_fullreport")],
        [types.InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])
    await callback.message.edit_text("<b>🔧 Utilities</b>\nSelect action:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("util_"))
async def execute_util(callback: types.CallbackQuery):
    """Execute utility command"""
    if not is_authorized(callback.from_user.id):
        return
    
    utils = {
        "util_reboot": ("🔄 Reboot", "sudo reboot", "⚠️ Reboot system?"),
        "util_shutdown": ("⏸️ Shutdown", "sudo shutdown -h now", "⚠️ Shutdown system?"),
        "util_clearcache": ("🗑️ Clear Cache", "sync; echo 3 > /proc/sys/vm/drop_caches", "Clear memory cache?"),
        "util_fullreport": ("📊 Full Report", "hostnamectl; free -h; df -h; uptime", "Create full report?")
    }
    
    util_name, command, confirm_text = utils.get(callback.data, (None, None, None))
    
    if util_name:
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Yes", callback_data=f"confirm_{command}")],
            [types.InlineKeyboardButton(text="❌ No", callback_data="main_menu")]
        ])
        await callback.message.edit_text(f"{confirm_text}", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_util(callback: types.CallbackQuery):
    """Confirm utility execution"""
    if not is_authorized(callback.from_user.id):
        return
    
    command = callback.data[8:]
    
    if command.startswith("sudo "):
        await callback.message.edit_text("🔐 <b>Sudo password required</b>\nEnter password to execute command:")
        user_states[callback.from_user.id] = {"mode": "wait_sudo_password", "sudo_command": command}
    else:
        await execute_command(callback.message, command)

@dp.callback_query(F.data == "main_menu")
async def main_menu_handler(callback: types.CallbackQuery):
    """Return to main menu"""
    if not is_authorized(callback.from_user.id):
        return
    
    user_id = callback.from_user.id
    if user_id in user_states:
        user_states.pop(user_id)
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📊 System Info", callback_data="sysinfo")],
        [types.InlineKeyboardButton(text="💾 Disk & Memory", callback_data="diskinfo")],
        [types.InlineKeyboardButton(text="🌐 Network & Internet", callback_data="networkinfo")],
        [types.InlineKeyboardButton(text="📁 File Manager", callback_data="files")],
        [types.InlineKeyboardButton(text="⚡ Processes", callback_data="processes")],
        [types.InlineKeyboardButton(text="🖥️ Terminal", callback_data="terminal")],
        [types.InlineKeyboardButton(text="🔧 Utilities", callback_data="utils")]
    ])
    
    await callback.message.edit_text("🖥️ <b>Host Control Panel</b>\nSelect section:", reply_markup=keyboard)

@dp.callback_query(F.data == "admin_menu")
async def admin_menu_handler(callback: types.CallbackQuery):
    """Return to admin menu"""
    if not is_authorized(callback.from_user.id):
        return
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📊 Bot Statistics", callback_data="admin_stats")],
        [types.InlineKeyboardButton(text="👥 User Management", callback_data="admin_users")],
        [types.InlineKeyboardButton(text="⚡ Command Management", callback_data="admin_commands")],
        [types.InlineKeyboardButton(text="📝 Bot Logs", callback_data="admin_logs")],
        [types.InlineKeyboardButton(text="🔄 Restart Bot", callback_data="admin_restart")],
        [types.InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])
    await callback.message.edit_text("⚙️ <b>Bot Admin Panel</b>\nSelect action:", reply_markup=keyboard)

def back_to_main_button():
    """Create back to main menu button"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])

def back_to_admin_button():
    """Create back to admin menu button"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_menu")]
    ])

async def main():
    """Main bot entry point"""
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
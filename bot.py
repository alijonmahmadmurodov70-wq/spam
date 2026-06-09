"""
Telegram Guruh Spam Filter Bot — v5.1 OPTIMIZED
- Guruhga xabar YUBORMASIN (faqat o'chir + log)
- URL/domen tekshiruvi set() bilan O(1)
- Normalize cache (lru_cache)
- Spam fragment set-based tez qidiruv
- Zero-width char bypass, unicode normalization
- Sticker/media spam detection
"""

import asyncio
import logging
import os
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
from functools import lru_cache

from dotenv import load_dotenv
from telegram import Update, ChatPermissions
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ChatMemberHandler, ContextTypes, filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", 0))
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "")

warnings:    dict[tuple[int, int], int]      = defaultdict(int)
muted_until: dict[tuple[int, int], datetime] = {}
BOT_ID: int = 0

# ════ ZERO-WIDTH VA KO'RINMAS BELGILAR ══════════════════════════

_INVISIBLE_CHARS = re.compile(
    r'[\u0000-\u001f\u007f-\u009f'
    r'\u00ad\u034f\u061c\u115f\u1160'
    r'\u17b4\u17b5\u180b-\u180e'
    r'\u200b-\u200f\u202a-\u202e'
    r'\u2060-\u2064\u2066-\u206f'
    r'\u3164\ufeff\uffa0'
    r'\U0001d173-\U0001d17a'
    r'\U000e0000-\U000e007f'
    r']+',
    re.UNICODE
)

# ════ CYRILLIC → LATIN ══════════════════════════════════════════

_CYRILLIC_MAP = {
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo',
    'ж':'j','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m',
    'н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u',
    'ф':'f','х':'x','ц':'ts','ч':'ch','ш':'sh','щ':'sch',
    'ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
    'ғ':'g','қ':'q','ң':'ng','ҳ':'h','ў':'o',
    'і':'i','ї':'i','є':'e',
}
_CYRILLIC_TABLE = str.maketrans(_CYRILLIC_MAP)

_RE_SEX_MEDICAL = re.compile(
    r'sex(?:ual|olog|gormon|appel|ed|ism|ist|ologi|ualit)',
    re.IGNORECASE
)

_RE_SPACES_BETWEEN = re.compile(r'\b((?:[a-z] ){2,}[a-z])\b')
_RE_PUNCT_BYPASS   = re.compile(r'(?<=[a-z])[.\-_*·•](?=[a-z])')
_RE_MULTI_SPACE    = re.compile(r'\s+')

_CHAR_REPLACE = str.maketrans({
    '0':'o','1':'i','3':'e','4':'a','5':'s','6':'b',
    '@':'a','$':'s','!':'i','|':'l','7':'t','+':'',
    'ı':'i','ñ':'n','ü':'u','ö':'o','ä':'a',
})

def clean_invisible(text: str) -> str:
    return _INVISIBLE_CHARS.sub('', text)

@lru_cache(maxsize=4096)
def normalize(text: str) -> str:
    if not text:
        return ""
    text = clean_invisible(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.lower().translate(_CYRILLIC_TABLE)
    text = _RE_PUNCT_BYPASS.sub('', text)
    text = _RE_SPACES_BETWEEN.sub(lambda m: m.group(0).replace(' ', ''), text)
    text = text.translate(_CHAR_REPLACE)
    text = _RE_MULTI_SPACE.sub(' ', text).strip()
    return text

# ════ SOʻKINISH SOʻZLARI ════════════════════════════════════════

SWEAR_WORDS_EXACT: list[str] = [
    "sex","seks",
    "orospu","fahsh","siktir","haromzoda","fohisha","nokas","nomard","beadab",
    "blyat","blyad","suka","pizda","huy","hui",
    "mudak","pidoras","zalupa","shlyukha","ublyudok","cyka","pidor","kurwa",
    "whore","bitch","fuck","cunt","dick","pussy","cock","ass",
]

SWEAR_WORDS_PARTIAL: list[str] = [
    "porn","porno","xxx",
    "yobany","nahuy","pohuy",
    "erotik","intim",
]

# Normalize qilingan variantlar (tezlik uchun oldindan tayyorla)
_SWEAR_EXACT_NORM:   list[str] = [normalize(w) for w in SWEAR_WORDS_EXACT]
_SWEAR_PARTIAL_NORM: list[str] = [normalize(w) for w in SWEAR_WORDS_PARTIAL]

# ════ SPAM FRAGMENTLAR ══════════════════════════════════════════

_RAW_SPAM_FRAGMENTS: list[str] = [
    # ── PROFIL SPAM ──
    "profilimda","profilimga","profilimni","profiliga","profilimga o",
    "profilga o","profiliga kir","profilga kir","profilimga qarang",
    "profilimga obuna","mening profilim","profilim orqali",
    "rofilimda","rofiliga","rrofilimda",
    "my profile","check my","visit my","see my profile",
    "open my profile","look at my profile","click my profile",
    "sahifamga","sahifamda","sahifaga o","sahifamga kir","mening sahifam",
    "bio da","bioda","bio'da","bio linkka","link in bio","link bio",
    "havola bio","linkka o't","linkka kir",
    # ── OBUNA / REKLAMA ──
    "obuna bo'l","obuna boling","obuna qiling","obuna bo'ling",
    "kanalimga","kanalga kir","kanalga ot","kanalga o't","kanalga qo",
    "guruhga qo","guruhga o't","chatga kir","guruhga kiring",
    "join channel","join group","join chat","join now","subscribe now",
    "подписывайтесь","подпишитесь","вступайте","присоединяйтесь",
    "follow me","follow now","follow back",
    # ── 18+ / ONLYFANS ──
    "onlyfans","only fans","fansly","loyalfans","justforfans",
    "adult content","private content","exclusive content",
    "premium content","vip content","nsfw content",
    "nude","nudelar","интим","эротика","порно","секс видео",
    "live sex","sex chat","sex call","cam girl","webcam show",
    "18+","adult video","adult photo",
    # ── TANISHUV / DATING ──
    "tanishamizmi","suhbatlashamizmi","yolg'iz qizlar","yolg'iz erkaklar",
    "pm ga yozing","ls ga yozing","lichkaga yoz","shaxsiyga yoz",
    "shaxsiy xabar","yozib qoling","menga yoz",
    "dating","date me","lonely girl","single girl","meet me",
    "знакомства","пиши в лс","пиши в личку","напиши мне",
    "пишите в личные","пиши лично",
    # ── PUL / DAROMAD SPAM ──
    "pul ishlash","pul topish","tez pul","oson pul","bepul daromad",
    "passive income","online daromad","oylik daromad",
    "kuniga 100","kuniga 200","kuniga 500","oyiga 1000","oyiga 5000",
    "earn money","make money","easy money","get rich","fast money",
    "заработок","заработай","быстрые деньги","пассивный доход",
    "легкий заработок","быстрый заработок",
    # ── TRADING / KRIPTO SPAM ──
    "trading signal","forex signal","kripto signal","crypto signal",
    "referral","promo kod","promo code","cashback",
    "vip signal","free signal","100% profit","guaranteed profit",
    "pump signal","dump signal","pump and dump",
    # ── KAZINO / BUKMAKER ──
    "kazino","casino","1win","mostbet","melbet","1xbet","pin-up","pinup",
    "vulkan casino","jackpot","gambling","free spin","freespin",
    "букмекер","казино","ставки","ставка","тотализатор",
    "sure bet","fixed match","guaranteed win","match fix",
    "leon bet","parimatch","betway","bwin","888sport",
    # ── KRIPTO / NFT / AIRDROP ──
    "airdrop","free nft","free token","free crypto","free bitcoin",
    "hamster kombat","notcoin","tapswap","blum airdrop",
    "tap to earn","play to earn","crypto mining",
    "presale","whitelist","ico launch","token sale",
    # ── PHISHING ──
    "hisobingiz bloklandi","akkauntingiz o'chir","parolingizni tasdiqlang",
    "sms kodni yuboring","account suspended","account blocked",
    "verify now","confirm your account","ваш аккаунт заблокирован",
    "click to verify","login required","urgent action",
    # ── UMUMIY REKLAMA ──
    "buyurtma bering","tez yetkazib berish","bepul yetkazib berish",
    "optom narxda","buy now","shop now","order now","click here",
    "limited offer","special offer","exclusive offer","act now",
]

# O(1) qidiruv uchun normalize qilingan set
SPAM_FRAGMENTS_NORM: set[str] = {normalize(f) for f in _RAW_SPAM_FRAGMENTS}
# Dinamik qo'shish uchun asl list (addspam komandasi)
SPAM_FRAGMENTS: list[str] = list(_RAW_SPAM_FRAGMENTS)

# ════ DOMENLAR VA HAVOLALAR ═════════════════════════════════════

_RAW_BLOCKED_DOMAINS: list[str] = [
    # URL shorteners
    "bit.ly","tinyurl.com","cutt.ly","is.gd","shorturl.at",
    "goo.gl","rb.gy","clck.ru","vk.cc","ow.ly","buff.ly",
    "tiny.cc","short.io","t.ly","bl.ink","rebrand.ly",
    "soo.gd","smarturl.it","shorte.st","adf.ly","bc.vc",
    # Adult
    "onlyfans.com","fansly.com","loyalfans.com","justforfans.com",
    "pornhub.com","xvideos.com","xhamster.com","redtube.com",
    # Link aggregators
    "linktr.ee","beacons.ai","solo.to","bio.link",
    # Kazino
    "1win.com","mostbet.com","melbet.com","1xbet.com",
]

# O(1) qidiruv uchun set
BLOCKED_DOMAINS_SET: set[str] = set(_RAW_BLOCKED_DOMAINS)
BLOCKED_DOMAINS: list[str] = list(_RAW_BLOCKED_DOMAINS)

_RE_URL        = re.compile(r'https?://([^\s/?\#]+)', re.IGNORECASE)
_RE_TG_INVITE  = re.compile(r't\.me/(?:joinchat/|\+|c/)[a-zA-Z0-9_\-]+', re.IGNORECASE)
_RE_TG_CHANNEL = re.compile(r't\.me/[a-zA-Z][a-zA-Z0-9_]{3,}', re.IGNORECASE)

SPAM_STICKER_SETS: set[str] = set()

# ════ SPAM TEKSHIRUVI (OPTIMIZED) ═══════════════════════════════

def is_spam(raw_text: str) -> tuple[bool, str]:
    if not raw_text:
        return False, ""

    raw_text = clean_invisible(raw_text)
    norm = normalize(raw_text)

    # 1. So'kinish — aniq so'z (word boundary)
    for w, nw in zip(SWEAR_WORDS_EXACT, _SWEAR_EXACT_NORM):
        if nw == "sex" and _RE_SEX_MEDICAL.search(raw_text):
            continue
        pattern = r'(?<![a-z\u0400-\u04ff])' + re.escape(nw) + r'(?![a-z\u0400-\u04ff])'
        if re.search(pattern, norm):
            return True, f"so'kinish: «{w}»"

    # 2. So'kinish — substring
    for w, nw in zip(SWEAR_WORDS_PARTIAL, _SWEAR_PARTIAL_NORM):
        if nw in norm:
            return True, f"so'kinish: «{w}»"

    # 3. Spam fragmentlar — O(1) set lookup
    for frag_norm in SPAM_FRAGMENTS_NORM:
        if frag_norm in norm:
            return True, f"spam fragment aniqlandi"

    # 4. Bloklangan domenlar — O(1) set lookup
    for m in _RE_URL.finditer(raw_text.lower()):
        domain = m.group(1).lstrip("www.")
        # To'liq domen yoki subdomain tekshiruvi
        for bd in BLOCKED_DOMAINS_SET:
            if domain == bd or domain.endswith('.' + bd):
                return True, f"bloklangan havola: {bd}"

    # 5. Telegram invite (joinchat/+/c/) — har doim spam
    if _RE_TG_INVITE.search(raw_text):
        return True, "Telegram kanal taklif havolasi"

    # 6. Oddiy t.me/username — faqat matn deyarli yo'q bo'lsa
    if _RE_TG_CHANNEL.search(raw_text):
        clean = _RE_TG_CHANNEL.sub("", raw_text)
        clean = re.sub(r'[^\w\s]', '', clean_invisible(clean)).strip()
        if len(clean) < 5:
            return True, "kanal reklama havolasi"

    return False, ""


def is_spam_sticker(sticker) -> tuple[bool, str]:
    if not sticker:
        return False, ""
    if sticker.set_name and sticker.set_name.lower() in SPAM_STICKER_SETS:
        return True, f"spam sticker: {sticker.set_name}"
    return False, ""


def is_bio_spam(bio: str) -> bool:
    if not bio:
        return False
    bio = clean_invisible(bio)
    norm = normalize(bio)
    bio_spam_norm = {normalize(w) for w in [
        "onlyfans","only fans","xxx","adult","fansly","loyalfans",
        "nude","porn","интим","эротика","порно",
        "profilimda","kanalimga","kazino","casino",
        "1win","mostbet","melbet","betting","gambling",
        "sex","seks","18+","nsfw","cam girl",
    ]}
    return any(w in norm for w in bio_spam_norm)

# ════ YORDAMCHI ══════════════════════════════════════════════════

def mention(user) -> str:
    if user.username:
        return f"@{user.username}"
    name = (user.full_name or "Foydalanuvchi").replace("<","&lt;").replace(">","&gt;")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


async def is_admin(chat_id: int, user_id: int, bot) -> bool:
    if user_id in (SUPER_ADMIN_ID, BOT_ID):
        return True
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except TelegramError:
        return False


async def try_delete(bot, chat_id: int, msg_id: int) -> bool:
    try:
        await bot.delete_message(chat_id, msg_id)
        return True
    except TelegramError as e:
        logger.error("O'CHIRA OLMADI chat=%d msg=%d: %s", chat_id, msg_id, e)
        return False


async def log_action(bot, chat_id: int, title: str, user, action: str, reason: str, text: str = ""):
    """Faqat log kanalga yozadi — guruhga xabar YUBORMASIN"""
    if not LOG_CHANNEL_ID:
        return
    icons = {"ban":"🔨 BAN","mute":"🔇 MUTE","warn":"⚠️ WARN","kick":"👢 KICK"}
    try:
        await bot.send_message(
            LOG_CHANNEL_ID,
            f"{icons.get(action,'📌')}\n"
            f"👤 {mention(user)} (<code>{user.id}</code>)\n"
            f"💬 {title} (<code>{chat_id}</code>)\n"
            f"📌 {reason}\n"
            f"⚠️ Ogohlantirish: {warnings[(user.id, chat_id)]} ta\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
            + (f"\n📝 <code>{text[:300]}</code>" if text else ""),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass


NO_PERMS = ChatPermissions(
    can_send_messages=False, can_send_polls=False,
    can_send_other_messages=False, can_add_web_page_previews=False,
    can_send_audios=False, can_send_documents=False,
    can_send_photos=False, can_send_videos=False,
    can_send_video_notes=False, can_send_voice_notes=False,
)
ALL_PERMS = ChatPermissions(
    can_send_messages=True, can_send_polls=True,
    can_send_other_messages=True, can_add_web_page_previews=True,
    can_invite_users=True, can_send_audios=True,
    can_send_documents=True, can_send_photos=True,
    can_send_videos=True, can_send_video_notes=True,
    can_send_voice_notes=True,
)

# ════ JAZO (GURUHGA XABAR YO'Q) ═════════════════════════════════

async def punish(bot, chat_id: int, title: str, user, reason: str, text: str = ""):
    """
    Spam topilganda:
    - 1-marta: 1 soat MUTE (guruhga xabar YO'Q)
    - 2-marta: doimiy BAN (guruhga xabar YO'Q)
    Faqat log kanalga yoziladi.
    """
    key = (user.id, chat_id)
    warnings[key] += 1
    count = warnings[key]

    if count == 1:
        # 1-MARTA → MUTE (xabarsiz)
        until = datetime.now() + timedelta(hours=1)
        muted_until[key] = until
        try:
            await bot.restrict_chat_member(
                chat_id, user.id,
                permissions=NO_PERMS,
                until_date=until
            )
            logger.info("MUTE user=%d chat=%d | %s", user.id, chat_id, reason)
        except TelegramError as e:
            logger.warning("Mute xatosi: %s", e)
        # Log kanalga yozamiz
        await log_action(bot, chat_id, title, user, "mute", reason, text)

    else:
        # 2-MARTA → BAN (xabarsiz)
        warnings[key] = 0
        muted_until.pop(key, None)
        try:
            await bot.ban_chat_member(chat_id, user.id)
            logger.info("BAN user=%d chat=%d | %s", user.id, chat_id, reason)
        except TelegramError as e:
            logger.warning("Ban xatosi: %s", e)
        # Log kanalga yozamiz
        await log_action(bot, chat_id, title, user, "ban", f"{reason} (2-marta)", text)

# ════ HANDLERLAR ════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not (msg and user and chat): return
    if chat.type == "private": return
    if user.is_bot or user.id == BOT_ID: return
    if await is_admin(chat.id, user.id, context.bot): return

    # ── Matn tekshiruvi (text + caption) ──
    text = (msg.text or msg.caption or "").strip()
    if text:
        found, reason = is_spam(text)
        if found:
            logger.warning("SPAM(text) | user=%d (@%s) | %s | %.80s",
                           user.id, user.username or "?", reason, text)
            await try_delete(context.bot, chat.id, msg.message_id)
            await punish(context.bot, chat.id, chat.title or str(chat.id), user, reason, text)
            return

    # ── Stiker tekshiruvi ──
    if msg.sticker:
        found, reason = is_spam_sticker(msg.sticker)
        if found:
            logger.warning("SPAM(sticker) | user=%d | %s", user.id, reason)
            await try_delete(context.bot, chat.id, msg.message_id)
            await punish(context.bot, chat.id, chat.title or str(chat.id), user, reason)
            return

    # ── Forward spam tekshiruvi ──
    if msg.forward_origin and text:
        found, reason = is_spam(text)
        if found:
            await try_delete(context.bot, chat.id, msg.message_id)
            await punish(context.bot, chat.id, chat.title or str(chat.id),
                         user, f"forward spam: {reason}", text)
            return

    # ── Inline keyboard URL tekshiruvi ──
    if msg.reply_markup:
        try:
            for row in msg.reply_markup.inline_keyboard:
                for btn in row:
                    if btn.url:
                        found, reason = is_spam(btn.url)
                        if found:
                            await try_delete(context.bot, chat.id, msg.message_id)
                            await punish(context.bot, chat.id, chat.title or str(chat.id),
                                         user, f"spam tugma: {reason}", btn.url)
                            return
        except Exception:
            pass


async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result: return
    new_m = result.new_chat_member
    chat  = result.chat
    if new_m.status not in ("member", "restricted"): return
    if new_m.user.is_bot: return

    user = new_m.user
    bio  = ""
    try:
        full = await context.bot.get_chat(user.id)
        bio  = getattr(full, "bio", "") or ""
    except TelegramError:
        pass

    if is_bio_spam(bio) or is_bio_spam(user.username or "") or is_bio_spam(user.full_name or ""):
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            await asyncio.sleep(1)
            await context.bot.unban_chat_member(chat.id, user.id)
            logger.info("KICK(bio spam) user=%d chat=%d", user.id, chat.id)
        except TelegramError as e:
            logger.warning("Kick xatosi: %s", e)
        await log_action(context.bot, chat.id, chat.title or "", user, "kick", "Spam bio/username", bio)

# ════ KOMANDALAR ════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or update.effective_chat.type != "private": return
    await msg.reply_text(
        "🤖 <b>Spam Filter Bot v5.1</b>\n\n"
        "✅ Spam xabarlarni avtomatik oʻchiradi\n"
        "🔕 Guruhga xabar <b>YUBORMAYDI</b> (yashirin ishlaydi)\n"
        "⚡ Optimized: O(1) domen va fragment qidiruvi\n"
        "🔍 Zero-width bypass tekshiruvi\n"
        "🧩 Stiker spam tekshiruvi\n"
        "👤 Spam bio → avtomatik chiqarish\n\n"
        "⚖️ <b>Jazo tizimi (yashirin):</b>\n"
        "⚠️ 1-spam → 1 soat mute (xabarsiz)\n"
        "🚫 2-spam → doimiy BAN (xabarsiz)\n"
        "📋 Faqat log kanalga yoziladi\n\n"
        "<b>Admin komandalari:</b>\n"
        "/test — huquqlar va spam bazasi\n"
        "/checkspam [matn] — spam tekshiruvi\n"
        "/addspam [soʻz] — yangi soʻz qoʻshish\n"
        "/warn [reply] — ogohlantirish\n"
        "/unwarn [reply] — tozalash\n"
        "/mute [reply] [1h/30m/1d] — mute\n"
        "/unmute [reply] — mute olib tashlash\n"
        "/ban [reply] [sabab] — ban\n"
        "/unban [reply] — ban olib tashlash\n"
        "/stats — statistika\n\n"
        "⚠️ <b>Botga kerakli huquqlar:</b>\n"
        "✅ Xabarlarni oʻchirish\n"
        "✅ Aʼzolarni cheklash\n"
        "✅ Aʼzolarni ban qilish",
        parse_mode=ParseMode.HTML,
    )


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not all([msg, user, chat]): return
    if not await is_admin(chat.id, user.id, context.bot): return

    can_del = can_res = False
    bot_name = "?"
    try:
        me = await context.bot.get_me()
        bot_name = me.username or str(me.id)
        bm = await context.bot.get_chat_member(chat.id, me.id)
        can_del = getattr(bm, "can_delete_messages", False)
        can_res = getattr(bm, "can_restrict_members", False)
    except TelegramError as e:
        await msg.reply_text(f"Xato: {e}")
        return

    if can_del and can_res:
        status = "✅ Barcha huquqlar mavjud!"
    else:
        status = ""
        if not can_del: status += "❌ Xabar oʻchirish huquqi YOʻQ!\n"
        if not can_res: status += "❌ Cheklash huquqi YOʻQ!\n"

    test_cases = [
        ("Men\u200bing\ufeff pr\u200cofilimda siz uc\u200bhun ko'p narsalar bor", True),
        ("Mening profilimda siz uchun ko'p narsalar bor", True),
        ("P.R.O.F.I.L.I.M.D.A", True),
        ("s e x", True),
        ("profilimga qarang", True),
        ("obuna bo'l", True),
        ("Salom hammaga qalaysizlar", False),
        ("Bugun ob-havo yaxshi", False),
        ("sexual harassment haqida gaplashamiz", False),
    ]
    test_lines = []
    for t, exp in test_cases:
        f, r = is_spam(t)
        icon = "✅" if f == exp else "❌"
        test_lines.append(f"{icon} {t[:45]!r}")

    cache_info = normalize.cache_info()
    await msg.reply_text(
        f"🤖 <b>@{bot_name} v5.1 OPTIMIZED</b>\n\n"
        f"{status}\n\n"
        f"📊 So'kinish: <b>{len(SWEAR_WORDS_EXACT)+len(SWEAR_WORDS_PARTIAL)}</b> | "
        f"Fragment: <b>{len(SPAM_FRAGMENTS)}</b> | "
        f"Domen: <b>{len(BLOCKED_DOMAINS)}</b>\n"
        f"⚡ Cache: hits={cache_info.hits} miss={cache_info.misses}\n\n"
        f"🧪 <b>Test natijalari:</b>\n" + "\n".join(test_lines),
        parse_mode=ParseMode.HTML,
    )


async def cmd_checkspam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not all([msg, user, chat]): return
    if not await is_admin(chat.id, user.id, context.bot): return
    if not context.args:
        await msg.reply_text("Ishlatish: /checkspam <matn>")
        return
    text  = " ".join(context.args)
    found, reason = is_spam(text)
    norm  = normalize(text)
    clean = clean_invisible(text)

    invisible_count = len(text) - len(clean)
    invisible_info  = f"\n👁 Ko'rinmas belgilar: <b>{invisible_count} ta</b>" if invisible_count else ""

    await msg.reply_text(
        f"{'🚨 SPAM!' if found else '✅ Spam emas'}\n\n"
        f"📝 Asl: <code>{text[:200]}</code>\n"
        f"🧹 Tozalangan: <code>{clean[:200]}</code>\n"
        f"🔄 Normalize: <code>{norm[:200]}</code>\n"
        f"📌 Sabab: {reason or '—'}"
        + invisible_info,
        parse_mode=ParseMode.HTML,
    )


async def cmd_addspam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not (msg and user): return
    if user.id != SUPER_ADMIN_ID: return
    if not context.args:
        await msg.reply_text("Ishlatish: /addspam <soʻz>")
        return
    kw = " ".join(context.args).lower().strip()
    kw_norm = normalize(kw)
    if kw_norm in SPAM_FRAGMENTS_NORM:
        await msg.reply_text(f"⚠️ «{kw}» allaqachon mavjud.")
    else:
        SPAM_FRAGMENTS.append(kw)
        SPAM_FRAGMENTS_NORM.add(kw_norm)
        await msg.reply_text(f"✅ «{kw}» qoʻshildi! Jami: {len(SPAM_FRAGMENTS)} ta")
    await try_delete(context.bot, chat.id, msg.message_id)


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not all([msg, user, chat]): return
    if not await is_admin(chat.id, user.id, context.bot): return
    if not (msg.reply_to_message and msg.reply_to_message.from_user):
        await msg.reply_text("Reply qilib yuboring.")
        return
    target = msg.reply_to_message.from_user
    if await is_admin(chat.id, target.id, context.bot):
        await msg.reply_text("❌ Adminni ogohlantirib boʻlmaydi.")
        return
    reason = " ".join(context.args) if context.args else "Admin tomonidan"
    await try_delete(context.bot, chat.id, msg.reply_to_message.message_id)
    await try_delete(context.bot, chat.id, msg.message_id)
    await punish(context.bot, chat.id, chat.title or "", target, f"⚠️ {reason}")


async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not all([msg, user, chat]): return
    if not await is_admin(chat.id, user.id, context.bot): return
    if not (msg.reply_to_message and msg.reply_to_message.from_user):
        await msg.reply_text("Reply qilib yuboring.")
        return
    target = msg.reply_to_message.from_user
    key = (target.id, chat.id)
    warnings[key] = 0
    muted_until.pop(key, None)
    try:
        await context.bot.restrict_chat_member(chat.id, target.id, permissions=ALL_PERMS)
    except TelegramError:
        pass
    await msg.reply_text(
        f"✅ {mention(target)} — ogohlantirishlari tozalandi.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not all([msg, user, chat]): return
    if not await is_admin(chat.id, user.id, context.bot): return
    if not (msg.reply_to_message and msg.reply_to_message.from_user):
        await msg.reply_text("Reply qilib yuboring.")
        return
    target = msg.reply_to_message.from_user
    if await is_admin(chat.id, target.id, context.bot):
        await msg.reply_text("❌ Adminni mute qilib boʻlmaydi.")
        return
    duration = timedelta(hours=1)
    label = "1 soat"
    if context.args:
        arg = context.args[0].lower()
        try:
            if   arg.endswith("h"): duration = timedelta(hours=int(arg[:-1]));   label = f"{arg[:-1]} soat"
            elif arg.endswith("m"): duration = timedelta(minutes=int(arg[:-1])); label = f"{arg[:-1]} daqiqa"
            elif arg.endswith("d"): duration = timedelta(days=int(arg[:-1]));    label = f"{arg[:-1]} kun"
        except ValueError:
            pass
    until = datetime.now() + duration
    try:
        await context.bot.restrict_chat_member(chat.id, target.id, permissions=NO_PERMS, until_date=until)
    except TelegramError as e:
        await msg.reply_text(f"❌ Xato: {e}")
        return
    muted_until[(target.id, chat.id)] = until
    await msg.reply_text(
        f"🔇 {mention(target)} — <b>{label} mute!</b>",
        parse_mode=ParseMode.HTML,
    )
    await log_action(context.bot, chat.id, chat.title or "", target, "mute", f"Admin mute: {label}")


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not all([msg, user, chat]): return
    if not await is_admin(chat.id, user.id, context.bot): return
    if not (msg.reply_to_message and msg.reply_to_message.from_user):
        await msg.reply_text("Reply qilib yuboring.")
        return
    target = msg.reply_to_message.from_user
    try:
        await context.bot.restrict_chat_member(chat.id, target.id, permissions=ALL_PERMS)
        muted_until.pop((target.id, chat.id), None)
    except TelegramError as e:
        await msg.reply_text(f"❌ Xato: {e}")
        return
    await msg.reply_text(
        f"✅ {mention(target)} — mute olib tashlandi.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not all([msg, user, chat]): return
    if not await is_admin(chat.id, user.id, context.bot): return
    if not (msg.reply_to_message and msg.reply_to_message.from_user):
        await msg.reply_text("Reply qilib yuboring.")
        return
    target = msg.reply_to_message.from_user
    if await is_admin(chat.id, target.id, context.bot):
        await msg.reply_text("❌ Adminni ban qilib boʻlmaydi.")
        return
    reason = " ".join(context.args) if context.args else "Admin tomonidan"
    try:
        await context.bot.ban_chat_member(chat.id, target.id)
        warnings[(target.id, chat.id)] = 0
    except TelegramError as e:
        await msg.reply_text(f"❌ Xato: {e}")
        return
    await msg.reply_text(
        f"🚫 {mention(target)} — <b>BAN!</b>\n📌 {reason}",
        parse_mode=ParseMode.HTML,
    )
    await log_action(context.bot, chat.id, chat.title or "", target, "ban", reason)


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not all([msg, user, chat]): return
    if not await is_admin(chat.id, user.id, context.bot): return
    if not (msg.reply_to_message and msg.reply_to_message.from_user):
        await msg.reply_text("Reply qilib yuboring.")
        return
    target = msg.reply_to_message.from_user
    try:
        await context.bot.unban_chat_member(chat.id, target.id, only_if_banned=True)
        warnings[(target.id, chat.id)] = 0
    except TelegramError as e:
        await msg.reply_text(f"❌ Xato: {e}")
        return
    await msg.reply_text(
        f"✅ {mention(target)} — ban olib tashlandi.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not all([msg, user, chat]): return
    if not await is_admin(chat.id, user.id, context.bot): return
    chat_w = {uid: cnt for (uid, cid), cnt in warnings.items() if cid == chat.id and cnt > 0}
    if not chat_w:
        await msg.reply_text("📊 Hozircha hech kim ogohlantirilmagan.")
        return
    lines = [f"📊 <b>{chat.title} — Ogohlantirishlar</b>\n"]
    for uid, cnt in sorted(chat_w.items(), key=lambda x: -x[1]):
        try:
            m2 = await context.bot.get_chat_member(chat.id, uid)
            name = mention(m2.user)
        except TelegramError:
            name = f"<code>{uid}</code>"
        lines.append(f"• {name} — <b>{cnt}</b> ta")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

# ════ POST INIT ══════════════════════════════════════════════════

async def post_init(application: Application) -> None:
    global BOT_ID
    me = await application.bot.get_me()
    BOT_ID = me.id
    logger.info("Bot: @%s (ID: %d)", me.username, me.id)
    logger.info("Fragment set: %d ta | Domen set: %d ta", len(SPAM_FRAGMENTS_NORM), len(BLOCKED_DOMAINS_SET))

# ════ MAIN ══════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN topilmadi! .env faylini tekshiring.")
    if not SUPER_ADMIN_ID:
        raise SystemExit("❌ SUPER_ADMIN_ID topilmadi! .env faylini tekshiring.")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("test",      cmd_test))
    app.add_handler(CommandHandler("checkspam", cmd_checkspam))
    app.add_handler(CommandHandler("addspam",   cmd_addspam))
    app.add_handler(CommandHandler("warn",      cmd_warn))
    app.add_handler(CommandHandler("unwarn",    cmd_unwarn))
    app.add_handler(CommandHandler("mute",      cmd_mute))
    app.add_handler(CommandHandler("unmute",    cmd_unmute))
    app.add_handler(CommandHandler("ban",       cmd_ban))
    app.add_handler(CommandHandler("unban",     cmd_unban))
    app.add_handler(CommandHandler("stats",     cmd_stats))

    app.add_handler(MessageHandler(
        ~filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_message,
    ))
    app.add_handler(ChatMemberHandler(
        handle_new_member,
        chat_member_types=ChatMemberHandler.CHAT_MEMBER,
    ))

    logger.info("=" * 60)
    logger.info("  Spam Filter Bot v5.1 OPTIMIZED ishga tushdi")
    logger.info("  GURUHGA XABAR YUBORMAYDI — yashirin ishlaydi")
    logger.info("  Fragment: %d | Domen: %d | So'kinish: %d",
                len(SPAM_FRAGMENTS), len(BLOCKED_DOMAINS),
                len(SWEAR_WORDS_EXACT) + len(SWEAR_WORDS_PARTIAL))
    logger.info("=" * 60)

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()

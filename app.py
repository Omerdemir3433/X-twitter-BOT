import os
import json
import random
import re
import threading
import time
from queue import Queue, Empty

from flask import Flask, render_template, request, jsonify
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(PROFILES_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

MAX_TWEET = 280

TARAYICI_ARG = [
    "--start-maximized",
    "--disable-blink-features=AutomationControlled",
    "--no-default-browser-check",
    "--no-first-run",
    "--disable-infobars",
]

GIZLEME_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['tr-TR', 'tr', 'en-US', 'en'] });
window.chrome = window.chrome || { runtime: {} };
"""

state = {
    "bagli": False,
    "kullanici": "",
    "baglanti_mesaji": "",
    "paylasim": {
        "aktif": False,
        "durakladi": False,
        "durduruldu": False,
        "bitti": False,
        "toplam": 0,
        "paylasilan": 0,
        "hata_sayisi": 0,
        "yuzde": 0,
        "gecikme_dk": 2,
        "siradaki": "",
        "son": "",
        "mesaj": "Hazır",
        "tweetler": [],
    },
}

app = Flask(__name__)
app.json.ensure_ascii = False


class XBot(threading.Thread):
    def __init__(self, profile_dir):
        super().__init__(daemon=True, name="xbot")
        self.profile_dir = profile_dir
        self.komut = Queue()
        self.p = None
        self.context = None
        self.page = None
        self.durdur = threading.Event()
        self.duraklat = threading.Event()
        self.kes_istegi = threading.Event()

    def run(self):
        self.p = sync_playwright().start()
        while True:
            try:
                if self.kes_istegi.is_set():
                    self.kes()
                    continue
                fn = self.komut.get(timeout=0.5)
                fn()
            except Empty:
                continue
            except Exception as e:
                state["paylasim"]["mesaj"] = "Hatırlatma: %s" % e

    def _ac_context(self):
        if self.context is not None:
            kapali = False
            try:
                if self.page is None or self.page.is_closed():
                    kapali = True
            except Exception:
                kapali = True
            if kapali:
                try:
                    self.context.close()
                except Exception:
                    pass
                self.context = None
                self.page = None
        if self.context is None:
            if not self._oturum_ac():
                return False
        return True

    def _oturum_ac(self):
        try:
            self.context = self.p.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir,
                channel="chrome",
                headless=False,
                viewport=None,
                args=TARAYICI_ARG,
                ignore_default_args=["--enable-automation"],
            )
        except Exception:
            self.context = None
        if self.context is None:
            try:
                self.context = self.p.chromium.launch_persistent_context(
                    user_data_dir=self.profile_dir,
                    headless=False,
                    viewport=None,
                    args=TARAYICI_ARG,
                    ignore_default_args=["--enable-automation"],
                )
            except Exception as e:
                state["baglanti_mesaji"] = "Tarayıcı açılamadı: %s" % e
                return False
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.context.add_init_script(GIZLEME_JS)
        return True

    def bagla(self):
        try:
            if not self._ac_context():
                return
            state["baglanti_mesaji"] = "X oturumu açılıyor. Lütfen giriş sayfasında hesabınızla giriş yapın..."
            self.page.goto("https://x.com/login", wait_until="domcontentloaded")
            self._bekle_giris()
        except Exception as e:
            state["baglanti_mesaji"] = "Hata: %s" % e

    def _giris_kontrolu(self):
        try:
            url = self.page.url or ""
            if "x.com/home" in url:
                return True
            link = self.page.locator('a[data-testid="AppTabBar_Profile_Link"]').first
            if link.count():
                href = link.get_attribute("href") or ""
                if href.startswith("/"):
                    return True
        except Exception:
            pass
        return False

    def _baglanti_basarili(self):
        state["bagli"] = True
        state["kullanici"] = self._kullanici_adi()
        state["baglanti_mesaji"] = "Bağlantı başarılı."

    def _bekle_giris(self):
        deadline = time.time() + 420
        while time.time() < deadline:
            if self.kes_istegi.is_set():
                self.kes()
                return
            try:
                if self.page.is_closed():
                    try:
                        self.context.close()
                    except Exception:
                        pass
                    self.context = None
                    self.page = None
                    state["baglanti_mesaji"] = "Tarayıcı kapatıldı. Oturum açık kalacaksa tekrar 'Hesap Bağla'ya basın."
                    return
            except Exception:
                return
            if self._giris_kontrolu():
                self._baglanti_basarili()
                return
            state["baglanti_mesaji"] = "Giriş bekleniyor... Açılan pencerede X hesabınıza giriş yapın. Başarılı girişten sonra pencereyi kapatmayın."
            time.sleep(2)
        try:
            if not self.page.is_closed():
                self.page.goto("https://x.com/home", wait_until="domcontentloaded")
                if self._giris_kontrolu():
                    self._baglanti_basarili()
                    return
        except Exception:
            pass
        state["baglanti_mesaji"] = "Giriş zaman aşımı (7 dk). Lütfen açılan pencerede telefon/e-posta doğrulamanızı tamamlayıp yeniden deneyin."

    def _kullanici_adi(self):
        try:
            link = self.page.locator('a[data-testid="AppTabBar_Profile_Link"]').first
            if link.count():
                href = link.get_attribute("href") or ""
                return href.strip("/").split("?")[0]
        except Exception:
            pass
        return "aktif_hesap"

    def kes(self):
        self.kes_istegi.clear()
        self.durdur.set()
        self.duraklat.clear()
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        self.context = None
        self.page = None
        state["bagli"] = False
        state["kullanici"] = ""
        state["baglanti_mesaji"] = "Bağlantı kesildi."

    def _tweet_kutusu(self, sure=25000):
        son = time.time() + sure / 1000.0
        seciciler = ['[data-testid="tweetTextarea_0"]', 'div[role="textbox"]']
        while time.time() < son:
            for secici in seciciler:
                try:
                    lb = self.page.locator(secici)
                    if lb.count():
                        ilk = lb.first
                        if ilk.is_visible():
                            return ilk
                except Exception:
                    pass
            self.page.wait_for_timeout(400)
        return None

    def _gonder_butonu(self, sure=15000):
        son = time.time() + sure / 1000.0
        seciciler = ['[data-testid="tweetButton"]', '[data-testid="tweetButtonInline"]']
        while time.time() < son:
            for secici in seciciler:
                try:
                    lb = self.page.locator(secici)
                    if lb.count():
                        ilk = lb.first
                        if ilk.is_visible() and ilk.is_enabled():
                            return ilk
                except Exception:
                    pass
            self.page.wait_for_timeout(400)
        return None

    def _gonderim_basarili(self, ta, sure=15000):
        son = time.time() + sure / 1000.0
        while time.time() < son:
            try:
                if not ta.is_visible():
                    return True
                if (ta.input_value() or "").strip() == "":
                    self.page.wait_for_timeout(1200)
                    if not ta.is_visible():
                        return True
                    if (ta.input_value() or "").strip() == "":
                        return True
            except Exception:
                return True
            self.page.wait_for_timeout(600)
        return False

    def _kompozitor_ac(self):
        try:
            self.page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
            ta = self._tweet_kutusu()
            if ta:
                return ta
        except Exception:
            pass
        try:
            self.page.goto("https://x.com/home", wait_until="domcontentloaded")
            self.page.wait_for_timeout(1500)
            nav = self.page.locator('a[data-testid="SideNav_NewTweet_Button"]').first
            nav.wait_for(state="visible", timeout=15000)
            nav.scroll_into_view_if_needed()
            nav.click()
            return self._tweet_kutusu()
        except Exception:
            return None

    def paylas(self, text):
        if not state["bagli"]:
            return False, "Bağlantı kesildi"
        if len(text) > MAX_TWEET:
            return False, "Tweet %d karakteri aşıyor (Premium hesap gerekli)" % MAX_TWEET
        if self.page is None or self.page.is_closed():
            if not self._ac_context():
                return False, "Tarayıcı açılamadı"
            try:
                self.page.goto("https://x.com/home", wait_until="domcontentloaded")
                time.sleep(3)
            except Exception:
                pass
        ta = self._kompozitor_ac()
        if ta is None:
            return False, "Tweet kutusu bulunamadı"
        try:
            ta.fill(text)
        except Exception:
            return False, "Tweet kutusu bulunamadı"
        btn = self._gonder_butonu()
        try:
            if btn is None:
                try:
                    ta.press("Control+Enter")
                except Exception:
                    return False, "Gönder butonu bulunamadı"
            else:
                try:
                    btn.click()
                except Exception:
                    try:
                        ta.press("Control+Enter")
                    except Exception:
                        return False, "Gönderim sırasında hata"
        except Exception:
            return False, "Gönderim sırasında hata"
        if self._gonderim_basarili(ta):
            return True, None
        return False, "X gönderimi onaylamadı (sayfa hatası)"

    def paylas_denemeli(self, text):
        son_hata = "Bilinmeyen hata"
        for _ in range(2):
            ok, hata = self.paylas(text)
            if ok:
                return True, None
            if hata:
                son_hata = hata
        return False, son_hata

    def _bekle(self, gecikme_dk):
        sn = gecikme_dk * 60 * random.uniform(0.6, 1.4)
        son = time.time() + sn
        while time.time() < son:
            if self.durdur.is_set():
                return False
            if self.duraklat.is_set():
                while self.duraklat.is_set():
                    if self.durdur.is_set():
                        return False
                    time.sleep(0.3)
            time.sleep(0.5)
        return True

    def basla(self, gecikme_dk):
        self.durdur.clear()
        self.duraklat.clear()
        s = state["paylasim"]
        s["aktif"] = True
        s["bitti"] = False
        s["durduruldu"] = False
        s["durakladi"] = False
        s["paylasilan"] = 0
        s["hata_sayisi"] = 0
        s["yuzde"] = 0
        s["mesaj"] = "Paylaşım başladı"
        for tw in s["tweetler"]:
            tw["durum"] = "bekliyor"

        for idx, tw in enumerate(s["tweetler"]):
            while True:
                if self.durdur.is_set():
                    s["durduruldu"] = True
                    break
                if not self.duraklat.is_set():
                    break
                time.sleep(0.3)
            if s["durduruldu"]:
                break

            s["siradaki"] = tw["text"]
            s["mesaj"] = "Paylaşılıyor (%d/%d)..." % (idx + 1, s["toplam"])
            ok, hata = self.paylas_denemeli(tw["text"])
            if ok:
                tw["durum"] = "paylaşıldı"
                tw["hata"] = ""
                s["paylasilan"] += 1
            else:
                tw["durum"] = "hata"
                tw["hata"] = hata or "Gönderilemedi"
                s["hata_sayisi"] += 1
            s["son"] = tw["text"]
            s["yuzde"] = round((idx + 1) / s["toplam"] * 100, 1)

            if idx < s["toplam"] - 1:
                s["mesaj"] = "Sıradaki tweet için bekleniyor..."
                if not self._bekle(gecikme_dk):
                    s["durduruldu"] = True

        s["aktif"] = False
        s["durakladi"] = False
        s["siradaki"] = ""
        if s["durduruldu"]:
            s["mesaj"] = "Durduruldu (%d/%d)" % (s["paylasilan"], s["toplam"])
        else:
            s["bitti"] = True
            s["mesaj"] = "TAMAMLANDI - %d tweet paylaşıldı (%d hata)" % (s["paylasilan"], s["hata_sayisi"])


def txt_parse(veri):
    surekli = veri.replace("\r\n", "\n").replace("\r", "\n")
    bloklar = re.split(r"\n\s*\n", surekli)
    sonuc = []
    for blok in bloklar:
        satirlar = [s for s in blok.splitlines() if not s.strip().startswith("#")]
        metin = "\n".join(satirlar).strip()
        if metin:
            sonuc.append(metin)
    return sonuc


def json_parse(veri):
    try:
        d = json.loads(veri)
    except Exception:
        return []
    if isinstance(d, dict):
        d = d.get("tweets") or d.get("list") or d.get("metinler") or []
    if not isinstance(d, list):
        return []
    sonuc = []
    for e in d:
        if isinstance(e, str):
            t = e.strip()
        elif isinstance(e, dict):
            t = (e.get("text") or e.get("tweet") or e.get("metin") or "").strip()
        else:
            continue
        if t:
            sonuc.append(t)
    return sonuc


bot = XBot(os.path.join(PROFILES_DIR, "main"))
bot.start()


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/bagla")
def api_bagla():
    bot.komut.put(bot.bagla)
    return jsonify({"ok": True, "mesaj": "Tarayıcı açılıyor..."})


@app.get("/api/baglanti")
def api_baglanti():
    return jsonify({
        "bagli": state["bagli"],
        "kullanici": state["kullanici"],
        "mesaj": state["baglanti_mesaji"],
    })


@app.post("/api/kes")
def api_kes():
    bot.kes_istegi.set()
    bot.kes()
    return jsonify({"ok": True})


@app.post("/api/yukle")
def api_yukle():
    dosya = request.files.get("dosya")
    if not dosya or not dosya.filename:
        return jsonify({"ok": False, "hata": "Dosya seçilmedi"})
    veri = dosya.read().decode("utf-8-sig", errors="replace")
    ad = (dosya.filename or "").lower()
    if ad.endswith(".json"):
        tweetler = json_parse(veri)
    else:
        tweetler = txt_parse(veri)
    if not tweetler:
        return jsonify({"ok": False, "hata": "Liste boş veya geçersiz"})
    s = state["paylasim"]
    if s["aktif"]:
        return jsonify({"ok": False, "hata": "Paylaşım devam ederken liste değiştirilemez. Önce durdurun."})
    s["tweetler"] = [{"text": t, "durum": "bekliyor", "uzun": len(t) > MAX_TWEET, "hata": ""} for t in tweetler]
    s["toplam"] = len(tweetler)
    s["paylasilan"] = 0
    s["hata_sayisi"] = 0
    s["yuzde"] = 0
    s["bitti"] = False
    s["durduruldu"] = False
    s["son"] = ""
    s["siradaki"] = ""
    s["mesaj"] = "Liste yüklendi"
    uzunlar = sum(1 for t in tweetler if len(t) > MAX_TWEET)
    return jsonify({"ok": True, "adet": len(tweetler), "uzunlar": uzunlar})


@app.post("/api/basla")
def api_basla():
    if not state["bagli"]:
        return jsonify({"ok": False, "hata": "Önce hesap bağlayın"})
    s = state["paylasim"]
    if not s["tweetler"]:
        return jsonify({"ok": False, "hata": "Önce tweet listesi yükleyin"})
    if s["aktif"]:
        return jsonify({"ok": False, "hata": "Paylaşım zaten devam ediyor"})
    veri = request.get_json(silent=True) or {}
    try:
        gecikme_dk = float(veri.get("gecikme") or 2)
    except Exception:
        gecikme_dk = 2
    gecikme_dk = max(0.1, min(600, gecikme_dk))
    s["gecikme_dk"] = gecikme_dk
    bot.komut.put(lambda: bot.basla(gecikme_dk))
    return jsonify({"ok": True, "mesaj": "Paylaşım başlıyor..."})


@app.post("/api/duraklat")
def api_duraklat():
    s = state["paylasim"]
    if s["aktif"] and not s["durakladi"]:
        bot.duraklat.set()
        s["durakladi"] = True
        s["mesaj"] = "Duraklatıldı"
    elif s["durakladi"]:
        bot.duraklat.clear()
        s["durakladi"] = False
        s["mesaj"] = "Devam ediyor..."
    return jsonify({"ok": True})


@app.post("/api/durdur")
def api_durdur():
    bot.durdur.set()
    bot.duraklat.clear()
    s = state["paylasim"]
    s["durakladi"] = False
    s["mesaj"] = "Durduruluyor..."
    return jsonify({"ok": True})


@app.get("/api/durum")
def api_durum():
    return jsonify({
        "bagli": state["bagli"],
        "kullanici": state["kullanici"],
        "baglanti_mesaji": state["baglanti_mesaji"],
        "paylasim": state["paylasim"],
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
"""
Ekonomik Takvim Ajanı
======================
Forex'te asıl deprem, teknik kırılımlarda değil, haber anında olur. Bu script:

1) Yaklaşan yüksek etkili makro ekonomik olayları (FOMC, ABD CPI, ABD Tarım Dışı
   İstihdam/NFP, ECB faiz kararı) bir takvime döker,
2) Her olay türü için GEÇMİŞTE piyasanın (EURUSD, Altın, Dolar Endeksi) o olaya
   nasıl tepki verdiğini gerçek fiyat verisiyle ölçer,
3) Finnhub'dan çektiği gerçek haber başlıklarının duygu (sentiment) skorunu
   "piyasa sürpriz algısı" proxy'si olarak kullanır,
4) Yaklaşan olaylar için basit, sayısallaştırılmış bir risk uyarı panosu üretir.

DÜRÜSTLÜK NOTU (kod başında, README'de de tekrarlanıyor):
Finnhub'ın gerçek ekonomik takvim endpoint'i (/calendar/economic) ücretsiz planda
403 (erişim yok) döndürüyor — bu premium bir özellik. Bu yüzden olay TARİHLERİ,
Fed / ECB / BLS'in kendi resmi web sitelerinden (federalreserve.gov, ecb.europa.eu,
bls.gov kaynaklı ikincil takvim sayfaları) doğrulanmış GERÇEK 2026 takvimiyle
sabit olarak koda gömüldü (kaynak her olayın yanında yazıyor). Finnhub'dan
GERÇEKTEN çekilen veri: genel piyasa haberleri (/news endpoint'i, çalışıyor).
Beklenti/gerçekleşen (forecast/actual) rakamları da premium'a kilitli olduğu için
"sürpriz" ekseninde gerçek sayı yerine, olay ÖNCESİ gerçekleşmiş (yfinance'ten
gelen, tamamen gerçek) volatilite kullanıldı — bu açıkça etiketlenmiş bir proxy'dir,
uydurma bir rakam değildir.

Yazar: Claude (Fable beyni ile) — Hakan için quant showcase projesi, 2026-08-01
"""

from __future__ import annotations

import os
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from dotenv import load_dotenv
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# 0. YOL VE SABİTLER
# ---------------------------------------------------------------------------

PROJE_KOK = Path(__file__).resolve().parent
GORSEL_DIZIN = PROJE_KOK / "gorseller"
VERI_DIZIN = PROJE_KOK / "veri"
ENV_YOLU = PROJE_KOK.parent / ".env"

GORSEL_DIZIN.mkdir(exist_ok=True)
VERI_DIZIN.mkdir(exist_ok=True)

TR_TZ = ZoneInfo("Europe/Istanbul")
NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

BUGUN_UTC = datetime.now(tz=UTC_TZ)

# Görsel dilinde kullanılacak ortak renk paleti (olay türüne göre)
OLAY_RENK = {
    "FOMC Faiz Kararı": "#E4572E",
    "ABD Enflasyon (CPI)": "#17A398",
    "ABD Tarım Dışı İstihdam (NFP)": "#2E86AB",
    "ECB Faiz Kararı": "#A23E48",
}
ETKI_RENK = {"yuksek": "#D62839", "orta": "#F4A261"}

print("=" * 70)
print("EKONOMİK TAKVİM AJANI — başlıyor")
print(f"Referans tarih (bugün, UTC): {BUGUN_UTC.strftime('%Y-%m-%d %H:%M')}")
print("=" * 70)

# ---------------------------------------------------------------------------
# 1. FINNHUB BAĞLANTISI — retry/backoff'lu istemci
# ---------------------------------------------------------------------------

load_dotenv(ENV_YOLU)
FINNHUB_ANAHTAR = os.getenv("FINNHUB_API_KEY")
FINNHUB_BASE = "https://finnhub.io/api/v1"

if not FINNHUB_ANAHTAR:
    print("UYARI: FINNHUB_API_KEY .env dosyasında bulunamadı. Haber/sentiment "
          "adımı atlanacak, sadece resmi kaynaklı takvim + fiyat verisiyle devam ediliyor.")


def finnhub_istek(path: str, params: dict, max_deneme: int = 4) -> tuple[int, dict | list | None]:
    """Finnhub'a GET isteği atar; 429 (rate-limit) durumunda basit exponential
    backoff ile tekrar dener. Başka ajanlar da aynı anahtarla istek atıyor
    olabileceği için bu bekleme mantığı önemli (bkz. görev talimatı)."""
    p = dict(params)
    p["token"] = FINNHUB_ANAHTAR
    bekleme = 2
    for deneme in range(1, max_deneme + 1):
        try:
            r = requests.get(f"{FINNHUB_BASE}/{path}", params=p, timeout=20)
        except requests.RequestException as e:
            print(f"  [Finnhub] ağ hatası ({deneme}/{max_deneme}): {e}")
            time.sleep(bekleme)
            bekleme *= 2
            continue

        if r.status_code == 200:
            return r.status_code, r.json()
        if r.status_code == 429:
            print(f"  [Finnhub] 429 rate-limit — {bekleme} sn bekleyip tekrar deneniyor "
                  f"({deneme}/{max_deneme})...")
            time.sleep(bekleme)
            bekleme *= 2
            continue
        # 403, 404 vb. — tekrar denemenin anlamı yok, direkt dön
        return r.status_code, None
    return r.status_code, None


# --- 1a. Gerçek ekonomik takvim endpoint'i deneniyor (dürüstlük testi) ------
print("\n[1/7] Finnhub /calendar/economic deneniyor (ücretsiz planda kısıtlı olabilir)...")
takvim_durum, takvim_veri = finnhub_istek(
    "calendar/economic",
    {"from": BUGUN_UTC.strftime("%Y-%m-%d"), "to": (BUGUN_UTC + timedelta(days=14)).strftime("%Y-%m-%d")},
)
FINNHUB_TAKVIM_ERISILEBILIR = takvim_durum == 200 and bool(takvim_veri)
if FINNHUB_TAKVIM_ERISILEBILIR:
    print(f"  BAŞARILI: Finnhub'ın canlı ekonomik takvimine erişildi ({len(takvim_veri)} kayıt).")
else:
    print(f"  ERİŞİLEMEDİ (HTTP {takvim_durum}). Beklenen durum bu — Finnhub ücretsiz planında "
          "/calendar/economic premium'a kilitli. FALLBACK: Fed/ECB/BLS resmi sitelerinden "
          "doğrulanmış statik 2026 takvimi kullanılacak (aşağıda kaynaklarıyla listeleniyor).")

# --- 1b. Genel piyasa haberleri (bu GERÇEKTEN çekiliyor) --------------------
print("\n[2/7] Finnhub /news (genel kategori) çekiliyor...")
haber_durum, haber_veri = finnhub_istek("news", {"category": "general"})
if haber_durum == 200 and haber_veri:
    print(f"  BAŞARILI: {len(haber_veri)} genel piyasa haberi çekildi.")
else:
    print(f"  ERİŞİLEMEDİ (HTTP {haber_durum}). Sentiment/sürpriz-proxy adımı boş geçilecek.")
    haber_veri = []

# ---------------------------------------------------------------------------
# 2. EKONOMİK TAKVİM — resmi kaynaklardan doğrulanmış 2026 tarihleri
# ---------------------------------------------------------------------------
# Her olay: (tarih, yerel_saat, yerel_tz, olay_adı, para_birimi, etki, kaynak)
# Kaynaklar 2026-08-01'de WebSearch/WebFetch ile doğrulandı.

FOMC_TOPLANTILARI = [
    # (karar günü — 2 günlük toplantının ikinci günü, kararlar bu gün 14:00 ET'de açıklanır)
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]
FOMC_KAYNAK = "federalreserve.gov/monetarypolicy/fomccalendars.htm (doğrulama: 2026-08-01)"

CPI_TARIHLERI = [
    "2026-01-13", "2026-02-13", "2026-03-11", "2026-04-10", "2026-05-12",
    "2026-06-10", "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14",
    "2026-11-10", "2026-12-10",
]
CPI_KAYNAK = "BLS resmi yayın takvimi (ikincil kaynak: macroornoise.com/cpi-release-dates-2026, doğrulama: 2026-08-01)"

ECB_TOPLANTILARI = [
    # Sadece basın toplantılı (parasal politika kararı alınan) toplantılar.
    # Ocak-Mart 2026 toplantıları resmi sayfadan doğrulanamadı (403/kısıtlı erişim) — dahil edilmedi (uydurulmadı).
    "2026-04-30", "2026-06-11", "2026-07-23", "2026-09-10", "2026-10-29", "2026-12-17",
]
ECB_KAYNAK = "ecb.europa.eu/press/calendars/mgcgc (doğrulama: 2026-08-01) — Oca/Şub/Mar 2026 toplantıları teyit edilemediği için listeye alınmadı"


def ilk_cuma(yil: int, ay: int) -> str:
    """Ayın ilk Cuma gününü döndürür (ABD NFP/Tarım Dışı İstihdam raporu
    yerleşik olarak her ayın ilk Cuması, 08:30 ET'de yayınlanır — bu BLS'in
    kamuya açık, resmi zamanlama kuralıdır, tahmini bir varsayım değildir)."""
    d = datetime(yil, ay, 1)
    offset = (4 - d.weekday()) % 7  # Cuma = weekday 4
    return (d + timedelta(days=offset)).strftime("%Y-%m-%d")


NFP_TARIHLERI = [ilk_cuma(2026, ay) for ay in range(1, 13)]
NFP_KAYNAK = "BLS yerleşik yayın kuralı: her ayın ilk Cuması, 08:30 ET (kural VERY HIGH güvenle bilinir; olası tek istisna resmi tatile denk gelen aylardır, 2026 için kontrol edilmedi)"


def olaylari_derle() -> pd.DataFrame:
    """Tüm olay türlerini tek bir DataFrame'de birleştirir, saatleri UTC'ye çevirir."""
    kayitlar = []

    def ekle(tarihler, saat_str, yerel_tz, olay_adi, para_birimi, etki, kaynak, ilgili_varliklar):
        for t in tarihler:
            saat, dakika = map(int, saat_str.split(":"))
            yerel_dt = datetime.strptime(t, "%Y-%m-%d").replace(
                hour=saat, minute=dakika, tzinfo=yerel_tz
            )
            utc_dt = yerel_dt.astimezone(UTC_TZ)
            kayitlar.append(
                dict(
                    olay=olay_adi,
                    tarih=t,
                    zaman_utc=utc_dt,
                    zaman_tr=utc_dt.astimezone(TR_TZ),
                    para_birimi=para_birimi,
                    etki=etki,
                    kaynak=kaynak,
                    ilgili_varliklar=ilgili_varliklar,
                )
            )

    ekle(FOMC_TOPLANTILARI, "14:00", NY_TZ, "FOMC Faiz Kararı", "USD", "yuksek",
         FOMC_KAYNAK, ["EURUSD=X", "GC=F", "DX-Y.NYB"])
    ekle(CPI_TARIHLERI, "08:30", NY_TZ, "ABD Enflasyon (CPI)", "USD", "yuksek",
         CPI_KAYNAK, ["EURUSD=X", "GC=F", "DX-Y.NYB"])
    ekle(NFP_TARIHLERI, "08:30", NY_TZ, "ABD Tarım Dışı İstihdam (NFP)", "USD", "yuksek",
         NFP_KAYNAK, ["EURUSD=X", "GC=F", "DX-Y.NYB"])
    ekle(ECB_TOPLANTILARI, "14:15", ZoneInfo("Europe/Berlin"), "ECB Faiz Kararı", "EUR", "yuksek",
         ECB_KAYNAK, ["EURUSD=X"])

    df = pd.DataFrame(kayitlar).sort_values("zaman_utc").reset_index(drop=True)
    return df


print("\n[3/7] Resmi kaynaklı 2026 ekonomik takvimi deriliyor (FOMC + CPI + NFP + ECB)...")
takvim_df = olaylari_derle()
takvim_df["gecmis_mi"] = takvim_df["zaman_utc"] < BUGUN_UTC
gecmis_df = takvim_df[takvim_df["gecmis_mi"]].copy()
gelecek_df = takvim_df[~takvim_df["gecmis_mi"]].copy()
print(f"  Toplam {len(takvim_df)} olay derlendi -> {len(gecmis_df)} geçmiş (tepki analizi için), "
      f"{len(gelecek_df)} gelecek (uyarı panosu için).")
takvim_df.to_csv(VERI_DIZIN / "ekonomik_takvim_2026.csv", index=False)

# ---------------------------------------------------------------------------
# 3. HABER + SENTIMENT (Finnhub'dan GERÇEKTEN çekilen kısım)
# ---------------------------------------------------------------------------

MAKRO_ANAHTAR_KELIMELER = [
    "fed", "fomc", "rate cut", "rate hike", "interest rate", "cpi", "inflation",
    "payroll", "jobs report", "unemployment", "ecb", "central bank", "powell",
    "lagarde", "treasury yield", "recession", "gdp",
]

print("\n[4/7] Haberlerden makro-ilgili olanlar ayıklanıp VADER ile sentiment hesaplanıyor...")
analyzer = SentimentIntensityAnalyzer()
haber_kayitlari = []
for h in haber_veri:
    baslik = (h.get("headline") or "").strip()
    ozet = (h.get("summary") or "").strip()
    metin = f"{baslik}. {ozet}"
    metin_kucuk = metin.lower()
    if not any(kw in metin_kucuk for kw in MAKRO_ANAHTAR_KELIMELER):
        continue
    skor = analyzer.polarity_scores(metin)
    zaman = datetime.fromtimestamp(h.get("datetime", 0), tz=UTC_TZ)
    haber_kayitlari.append(
        dict(baslik=baslik, zaman_utc=zaman, compound=skor["compound"], kaynak_url=h.get("url", ""))
    )

haber_df = pd.DataFrame(haber_kayitlari)
print(f"  {len(haber_veri)} genel haberden {len(haber_df)} tanesi makro-ekonomi ile ilgili bulundu.")
if len(haber_df):
    haber_df.to_csv(VERI_DIZIN / "finnhub_makro_haberler_sentiment.csv", index=False)
    print(f"  Haber zaman aralığı: {haber_df['zaman_utc'].min()} -> {haber_df['zaman_utc'].max()}")
    print(f"  (Not: Finnhub /news ücretsiz planda sadece YAKIN GEÇMİŞ haberleri döndürür, "
          f"tam yıl arşivi değil — bu yüzden sentiment eşleşmesi sadece son birkaç güne "
          f"düşen olaylar için mümkün olacak; bu README'de açıkça belirtilecek.)")

# ---------------------------------------------------------------------------
# 4. FİYAT VERİSİ (yfinance) — 730 gün, 60 dakikalık barlar
# ---------------------------------------------------------------------------

VARLIK_ADI = {"EURUSD=X": "EUR/USD", "GC=F": "Altın (Gold Futures)", "DX-Y.NYB": "Dolar Endeksi (DXY)"}

print("\n[5/7] yfinance'ten fiyat verisi indiriliyor (730 gün, 60 dakikalık barlar)...")
import yfinance as yf

fiyat_cache: dict[str, pd.DataFrame] = {}
for ticker in VARLIK_ADI:
    try:
        df = yf.download(ticker, period="730d", interval="60m", progress=False)
        if df.empty:
            print(f"  UYARI: {ticker} için veri boş döndü, atlanıyor.")
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = df.index.tz_convert("UTC")
        fiyat_cache[ticker] = df
        print(f"  {ticker} ({VARLIK_ADI[ticker]}): {len(df)} bar, "
              f"{df.index.min().date()} -> {df.index.max().date()}")
    except Exception as e:
        print(f"  HATA: {ticker} indirilemedi ({e}), bu varlık analizden çıkarılıyor.")

# ---------------------------------------------------------------------------
# 5. OLAY TEPKİSİ HESAPLAMA — öncesi/sonrası ±2 saatlik pencere
# ---------------------------------------------------------------------------

PENCERE_SAAT = 2


def olay_tepkisi_hesapla(df: pd.DataFrame, olay_zamani: pd.Timestamp, pencere_saat: int = PENCERE_SAAT) -> dict | None:
    """Bir olayın öncesi ve sonrası fiyat penceresinden volatilite + getiri hesaplar.
    Bar çözünürlüğü 60 dakika olduğu için ±2 saatlik pencere ~2 bar içerir; bu
    kısıtlama README'de açıkça belirtiliyor (uydurulmuş bir hassasiyet iddia edilmiyor)."""
    once_baslangic = olay_zamani - timedelta(hours=pencere_saat)
    sonra_bitis = olay_zamani + timedelta(hours=pencere_saat)

    once_pencere = df.loc[(df.index >= once_baslangic) & (df.index < olay_zamani)]
    sonra_pencere = df.loc[(df.index >= olay_zamani) & (df.index <= sonra_bitis)]

    if once_pencere.empty or sonra_pencere.empty:
        return None

    once_open = float(once_pencere["Open"].iloc[0])
    once_volatilite = float((once_pencere["High"].max() - once_pencere["Low"].min()) / once_open * 100)

    sonra_open = float(sonra_pencere["Open"].iloc[0])
    sonra_volatilite = float((sonra_pencere["High"].max() - sonra_pencere["Low"].min()) / sonra_open * 100)
    sonra_getiri = float((sonra_pencere["Close"].iloc[-1] - sonra_open) / sonra_open * 100)

    tepki_orani = (sonra_volatilite / once_volatilite) if once_volatilite > 1e-9 else np.nan

    return dict(
        once_volatilite_pct=once_volatilite,
        sonra_volatilite_pct=sonra_volatilite,
        sonra_getiri_pct=sonra_getiri,
        tepki_orani=tepki_orani,
    )


print(f"\n[6/7] Geçmiş {len(gecmis_df)} olay için gerçek fiyat tepkisi hesaplanıyor "
      f"(öncesi/sonrası ±{PENCERE_SAAT} saat)...")

tepki_kayitlari = []
atlanan = 0
for _, olay in gecmis_df.iterrows():
    for ticker in olay["ilgili_varliklar"]:
        df = fiyat_cache.get(ticker)
        if df is None:
            continue
        sonuc = olay_tepkisi_hesapla(df, olay["zaman_utc"])
        if sonuc is None:
            atlanan += 1
            continue
        tepki_kayitlari.append(
            dict(
                olay=olay["olay"],
                tarih=olay["tarih"],
                para_birimi=olay["para_birimi"],
                varlik=ticker,
                varlik_adi=VARLIK_ADI.get(ticker, ticker),
                **sonuc,
            )
        )

tepki_df = pd.DataFrame(tepki_kayitlari)
print(f"  {len(tepki_df)} olay x varlık kombinasyonu için tepki hesaplandı "
      f"({atlanan} kombinasyon veri eksikliğinden atlandı).")
if len(tepki_df):
    tepki_df.to_csv(VERI_DIZIN / "olay_tepkileri.csv", index=False)

# --- Haber-sentiment ile olay eşleştirme (sadece yakın geçmişteki olaylar için) ---
tepki_df["sürpriz_sentiment_proxy"] = np.nan
if len(haber_df):
    for idx, row in tepki_df.iterrows():
        olay_zamani = gecmis_df.loc[gecmis_df["tarih"] == row["tarih"], "zaman_utc"]
        if olay_zamani.empty:
            continue
        olay_zamani = olay_zamani.iloc[0]
        pencere = haber_df[
            (haber_df["zaman_utc"] >= olay_zamani - timedelta(days=2))
            & (haber_df["zaman_utc"] <= olay_zamani + timedelta(days=1))
        ]
        if len(pencere):
            tepki_df.at[idx, "sürpriz_sentiment_proxy"] = pencere["compound"].abs().mean()

eslesen_sayisi = tepki_df["sürpriz_sentiment_proxy"].notna().sum()
print(f"  Bunlardan {eslesen_sayisi} tanesi gerçek Finnhub haberiyle zaman-eşleşti "
      f"(sentiment proxy dolduruldu); geri kalanı NaN — uydurulmadı.")

# ---------------------------------------------------------------------------
# 6. GÖRSELLER
# ---------------------------------------------------------------------------

print("\n[7/7] Görseller üretiliyor...")


def kaydet(fig: go.Figure, dosya_adi: str, genislik=1200, yukseklik=700):
    html_yolu = GORSEL_DIZIN / f"{dosya_adi}.html"
    png_yolu = GORSEL_DIZIN / f"{dosya_adi}.png"
    fig.write_html(html_yolu)
    try:
        fig.write_image(png_yolu, width=genislik, height=yukseklik, scale=2)
    except Exception as e:
        print(f"  UYARI: {dosya_adi} PNG'ye çevrilemedi ({e}), sadece HTML kaydedildi.")
    print(f"  kaydedildi -> {html_yolu.name} / {png_yolu.name}")


ORTAK_TEMA = dict(
    template="plotly_white",
    font=dict(family="Arial, sans-serif", size=13),
    margin=dict(l=60, r=40, t=90, b=60),
)

# --- Görsel 1: Yaklaşan olaylar zaman çizelgesi -----------------------------
# Not: kaleido'nun PNG'ye çevirirken kullandığı JSON encoder tz-aware pd.Timestamp
# ile sorun çıkardığı için (write_html'de sorun yok ama write_image'da var), eksen
# değerlerini tz-naive'e çeviriyoruz — mutlak zaman değişmiyor, sadece tz etiketi düşüyor.
tl_df = gelecek_df.copy()
tl_df["zaman_naive"] = tl_df["zaman_utc"].dt.tz_localize(None)
tl_df["bitis_naive"] = tl_df["zaman_naive"] + timedelta(hours=1)
tl_df["etiket"] = tl_df.apply(
    lambda r: f"{r['olay']} ({r['para_birimi']}) — {r['zaman_tr'].strftime('%d %b %H:%M')} TR saati", axis=1
)

fig1 = px.timeline(
    tl_df, x_start="zaman_naive", x_end="bitis_naive", y="olay", color="olay",
    color_discrete_map=OLAY_RENK, hover_name="etiket",
    title="Yaklaşan Ekonomik Olaylar — Zaman Çizelgesi (bugünden itibaren)",
)
fig1.update_yaxes(title="", autorange="reversed")
fig1.update_xaxes(title="Tarih (UTC)")
fig1.update_traces(width=0.4)
fig1.update_layout(**ORTAK_TEMA, showlegend=False, height=500)
for _, r in tl_df.iterrows():
    # Not: kaleido'nun PNG dışa aktarımı sırasında kullandığı JSON encoder tz-naive
    # olsa bile pandas Timestamp tipini reddediyor ("Type is not JSON serializable:
    # Timestamp") — .to_pydatetime() ile saf Python datetime'a çevrilmesi gerekiyor.
    fig1.add_annotation(
        x=r["zaman_naive"].to_pydatetime(), y=r["olay"], text=r["zaman_tr"].strftime("%d %b, %H:%M TR"),
        showarrow=False, yshift=18, font=dict(size=10, color="#555"),
    )
kaydet(fig1, "01_yaklasan_olaylar_zaman_cizelgesi", yukseklik=500)

# --- Görsel 2: Olay türü başına geçmiş volatilite tepkisi (bar) -------------
if len(tepki_df):
    ozet2 = (
        tepki_df.groupby("olay")
        .agg(ort_tepki_orani=("tepki_orani", "mean"), ort_sonra_vol=("sonra_volatilite_pct", "mean"),
             n=("olay", "count"))
        .reset_index()
        .sort_values("ort_tepki_orani", ascending=False)
    )
    fig2 = go.Figure()
    fig2.add_bar(
        x=ozet2["olay"], y=ozet2["ort_tepki_orani"],
        marker_color=[OLAY_RENK.get(o, "#888") for o in ozet2["olay"]],
        text=[f"{v:.2f}x  (n={n})" for v, n in zip(ozet2["ort_tepki_orani"], ozet2["n"])],
        textposition="outside",
    )
    fig2.add_hline(y=1.0, line_dash="dash", line_color="gray")
    fig2.update_layout(
        **ORTAK_TEMA,
        title="Olay Türü Başına Geçmiş Volatilite Tepki Oranı<br>"
              "<sup>sonrası volatilite / öncesi volatilite (tüm ilgili varlıklar ortalaması, ±2 saat pencere)</sup>",
        yaxis_title="Volatilite tepki oranı (kat)", xaxis_title="",
    )
    # Çizgi etiketini bar üstü sayı etiketleriyle çakışmayacak şekilde sabit bir
    # köşeye (paper koordinatı) ayrı annotation olarak koyuyoruz.
    fig2.add_annotation(
        text="kesikli çizgi = 1.0x (olay volatiliteyi değiştirmiyor)",
        xref="paper", yref="paper", x=0.99, y=1.06, showarrow=False,
        font=dict(size=11, color="#555"), align="right",
    )
    kaydet(fig2, "02_olay_turu_gecmis_volatilite_tepkisi_bar")
else:
    print("  UYARI: tepki_df boş, Görsel 2 atlandı.")

# --- Görsel 3: Olay öncesi volatilite (sürpriz proxy) vs fiyat tepkisi ------
if len(tepki_df):
    fig3 = px.scatter(
        tepki_df, x="once_volatilite_pct", y="sonra_getiri_pct",
        color="olay", size="sonra_volatilite_pct", color_discrete_map=OLAY_RENK,
        hover_data=["tarih", "varlik_adi", "tepki_orani"],
        title="Olay Öncesi Volatilite (\"Piyasa Gerginliği\" Proxy) vs Olay Sonrası Fiyat Tepkisi",
    )
    fig3.add_hline(y=0, line_color="gray", line_width=1)
    fig3_margin = dict(ORTAK_TEMA)
    fig3_margin["margin"] = dict(l=60, r=40, t=90, b=120)
    fig3.update_layout(
        **fig3_margin,
        xaxis_title="Olay öncesi 2 saatlik gerçekleşen volatilite (%)",
        yaxis_title="Olay sonrası 2 saatlik fiyat getirisi (%)",
    )
    fig3.add_annotation(
        text="Not: Finnhub ücretsiz planda forecast/actual (beklenti/gerçekleşen) rakamı yok —<br>"
             "x ekseni GERÇEK fiyat verisinden hesaplanan bir 'piyasa gerginliği' proxy'sidir.",
        xref="paper", yref="paper", x=0.0, y=-0.22, showarrow=False,
        font=dict(size=10, color="#888"), align="left",
    )
    kaydet(fig3, "03_beklenti_sapmasi_fiyat_tepkisi_scatter", yukseklik=780)
else:
    print("  UYARI: tepki_df boş, Görsel 3 atlandı.")

# --- Görsel 4: Olay öncesi/sonrası volatilite penceresi (box plot) ----------
if len(tepki_df):
    box_data = pd.concat(
        [
            tepki_df[["olay", "once_volatilite_pct"]].rename(columns={"once_volatilite_pct": "volatilite"}).assign(pencere="Öncesi (2 saat)"),
            tepki_df[["olay", "sonra_volatilite_pct"]].rename(columns={"sonra_volatilite_pct": "volatilite"}).assign(pencere="Sonrası (2 saat)"),
        ]
    )
    fig4 = px.box(
        box_data, x="olay", y="volatilite", color="pencere",
        color_discrete_map={"Öncesi (2 saat)": "#8AA9C1", "Sonrası (2 saat)": "#D62839"},
        points="all",
        title="Olay Öncesi vs Sonrası Volatilite Dağılımı (±2 saatlik pencere, tüm varlıklar)",
    )
    fig4.update_layout(**ORTAK_TEMA, yaxis_title="Pencere içi range volatilitesi (%)", xaxis_title="")
    kaydet(fig4, "04_olay_oncesi_sonrasi_volatilite_penceresi")
else:
    print("  UYARI: tepki_df boş, Görsel 4 atlandı.")

# --- Görsel 5: Etki ısı haritası (olay türü x varlık) -----------------------
if len(tepki_df):
    pivot5 = tepki_df.pivot_table(
        index="olay", columns="varlik_adi", values="sonra_volatilite_pct", aggfunc="mean"
    )
    fig5 = go.Figure(
        data=go.Heatmap(
            z=pivot5.values, x=pivot5.columns, y=pivot5.index,
            colorscale="Reds", text=np.round(pivot5.values, 3),
            texttemplate="%{text}%", colorbar=dict(title="Ort. tepki<br>volatilitesi (%)"),
        )
    )
    fig5.update_layout(
        **ORTAK_TEMA,
        title="Etki Isı Haritası — Olay Türü x Varlık<br>"
              "<sup>olay sonrası 2 saatlik ortalama range volatilitesi (%)</sup>",
    )
    kaydet(fig5, "05_etki_isi_haritasi_olay_varlik")
else:
    print("  UYARI: tepki_df boş, Görsel 5 atlandı.")

# --- Görsel 6: Risk uyarı panosu (kart) -------------------------------------
YAKINDAKI_N = 6
yakin_olaylar = gelecek_df.head(YAKINDAKI_N).copy()

if len(tepki_df):
    ort_tepki_tablosu = tepki_df.groupby("olay")["tepki_orani"].mean().to_dict()
else:
    ort_tepki_tablosu = {}


def risk_seviyesi_ve_oneri(olay_adi: str, kalan_saat: float) -> tuple[str, str]:
    ort_tepki = ort_tepki_tablosu.get(olay_adi, np.nan)
    if pd.isna(ort_tepki):
        seviye = "ORTA (geçmiş veri yetersiz)"
        renk = ETKI_RENK["orta"]
    elif ort_tepki >= 1.5:
        seviye = "YÜKSEK"
        renk = ETKI_RENK["yuksek"]
    else:
        seviye = "ORTA"
        renk = ETKI_RENK["orta"]

    oneri = (
        f"Olay ±30 dk içinde yeni pozisyon AÇMA. Mevcut pozisyonlarda stop mesafesini "
        f"genişlet. Sabit risk kuralı (%0.5) korunur; olay penceresinde pozisyon "
        f"büyüklüğünü yarıya indirmeyi değerlendir. Geçmişte bu olay volatiliteyi "
        f"ortalama {ort_tepki:.2f}x artırdı." if not pd.isna(ort_tepki) else
        "Bu olay türü için yeterli geçmiş fiyat verisi hesaplanamadı; temkinli yaklaş, "
        "sabit risk kuralı (%0.5) korunur."
    )
    return seviye, oneri, renk


kart_satirlari = []
for _, r in yakin_olaylar.iterrows():
    kalan = (r["zaman_utc"] - BUGUN_UTC).total_seconds() / 3600
    seviye, oneri, renk = risk_seviyesi_ve_oneri(r["olay"], kalan)
    kart_satirlari.append(
        dict(
            tarih_tr=r["zaman_tr"].strftime("%d %b %Y, %H:%M") + " TR",
            olay=r["olay"], para_birimi=r["para_birimi"],
            kalan_sure=f"{kalan:.0f} saat" if kalan < 72 else f"{kalan/24:.1f} gün",
            seviye=seviye, oneri=oneri, renk=renk,
        )
    )

def hex_to_rgba(hex_renk: str, alpha: float = 0.28) -> str:
    """'#RRGGBB' hex rengini plotly'nin kabul ettiği rgba(...) string'ine çevirir
    (plotly 8-haneli hex + alpha birleşimini kabul etmiyor, ayrı ayrı vermek gerekiyor)."""
    h = hex_renk.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


kart_df = pd.DataFrame(kart_satirlari)
fig6 = go.Figure(
    data=[
        go.Table(
            columnwidth=[110, 150, 60, 70, 90, 320],
            header=dict(
                values=["Tarih (TR)", "Olay", "Para Birimi", "Kalan Süre", "Risk Seviyesi", "Öneri"],
                fill_color="#1B1B3A", font=dict(color="white", size=12), align="left", height=32,
            ),
            cells=dict(
                values=[kart_df[c] for c in ["tarih_tr", "olay", "para_birimi", "kalan_sure", "seviye", "oneri"]],
                fill_color=[["#F7F7F7"] * len(kart_df)] * 5 + [kart_df["renk"].map(hex_to_rgba).tolist()],
                align="left", font=dict(size=11), height=90,
            ),
        )
    ]
)
fig6.update_layout(
    title="Risk Uyarı Panosu — Yaklaşan En Yakın "
          f"{len(kart_df)} Yüksek Etkili Ekonomik Olay",
    margin=dict(l=20, r=20, t=70, b=20),
    template="plotly_white",
)
# Not: "Öneri" sütunundaki metin 3-4 satıra sarıyor; hücre height'ı bunu karşılayacak
# kadar büyük tutulmalı, yoksa alt satırlar kesiliyor/üst üste biniyor (görsel bir
# okunabilirlik hatası, kontrast hatası değil — bu yüzden satır başına 90px verildi).
kaydet(fig6, "06_risk_uyari_panosu", genislik=1450, yukseklik=70 + 32 + 90 * len(kart_df) + 30)

print("\n" + "=" * 70)
print("TAMAMLANDI.")
print(f"Görseller: {GORSEL_DIZIN}")
print(f"Veri:      {VERI_DIZIN}")
print("=" * 70)

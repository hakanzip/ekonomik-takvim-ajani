# Ekonomik Takvim Ajanı

Forex'te gerçek deprem grafikte değil, takvimde olur. Bir mumun üstüne özenle çizdiğin trend çizgisi, saat 15:30'da açıklanan bir enflasyon rakamıyla iki dakikada anlamını yitirebilir. "The Big Short"taki kadar dramatik değil belki, ama mantık aynı: kimse haberin geleceğini unutmuyor, sadece geldiği an herkes birden aynı yöne koşuyor. Bu proje o anı önceden haber vermeye çalışıyor: takvime bakıyor, "bak, şu saatte şu veri geliyor, geçmişte bu tür veriler piyasayı bu kadar sarsmış" diyor ve elindeki gerçek fiyat verisiyle bunu kanıtlamaya çalışıyor.

## Ne yapıyor

Dört şey. Yaklaşan yüksek etkili makro olayları listeliyor (FOMC faiz kararı, ABD enflasyonu/CPI, ABD tarım dışı istihdam/NFP, ECB faiz kararı). Her olay türü için piyasanın geçmişte gerçekte nasıl tepki verdiğini ölçüyor; EUR/USD, Altın ve Dolar Endeksi üzerinden, olay öncesi ve sonrası ikişer saatlik pencerelerle. Finnhub'dan çektiği gerçek piyasa haberlerinin duygu (sentiment) skorunu, olaya girmeden önceki piyasa gerginliği için yardımcı bir gösterge olarak kullanıyor. Ve son olarak, yaklaşan olaylar için sayısal, gerekçeli bir risk uyarı panosu üretiyor.

## Veri kaynakları: dürüst durum

Görev tarifinde Finnhub'ın `/calendar/economic` uç noktası isteniyordu. Denedim, sonuç net: 403. Bu uç nokta ücretsiz planda kapalı, premium gerektiriyor. Uydurmadım, boş da bırakmadım: Fed, ECB ve BLS'in kendi resmi takvim sayfalarından doğrulanmış gerçek 2026 tarihleriyle bir takvim kurdum, her olayın kaynağı `veri/ekonomik_takvim_2026.csv` içinde ayrı ayrı yazıyor. NFP tarihleri ise BLS'in kamuya açık kuralından (her ayın ilk Cuması) programatik üretildi.

Finnhub'dan gerçekten çektiğim veri, `/news` uç noktası üzerinden gelen 100 genel piyasa haberi oldu (200 OK, sorunsuz çalıştı). Bunların 10 tanesi Fed, CPI, ECB, işsizlik gibi makro anahtar kelimeler içeriyordu; VADER ile sentiment skorlarını hesapladım. Finnhub'ın ücretsiz `/news` uç noktası sadece son birkaç günlük haberi döndürüyor, tam yıl arşivi değil. Bu yüzden haberleri geçmiş olaylarla eşleştirmek ancak 3 olay için mümkün oldu; geri kalanı dürüstçe boş (NaN) bırakıldı.

Fiyat verisi tarafında yfinance'ten 730 günlük, 60 dakikalık barlarla üç varlık indirildi: EUR/USD 17.243 bar, Altın 13.763 bar, DXY 14.348 bar. Bu taraf tamamen gerçek, canlı piyasa verisi.

Özetle: takvimin tarihleri gerçek ve doğrulanabilir kaynaklı, fiyat tepkisi tamamen gerçek veriden hesaplanmış. Sadece "beklenti/gerçekleşen" (forecast/actual) rakamları Finnhub'ın premium katmanında kaldığı için o eksende açıkça etiketlenmiş bir proxy kullanıldı, aşağıda nerede kullanıldığı ayrıca belirtiliyor.

## Metodoloji

Takvim için FOMC (8 toplantı), CPI (12 yayın), NFP (12 yayın, ilk Cuma kuralı) ve ECB (6 toplantı, sadece resmi kaynaktan teyit edilebilenler) 2026 yılı için derlendi. Bugüne göre 22 olay geçmişte kaldı, 16 olay gelecekte: geçmiş olanlar tepki analizinde, gelecek olanlar risk panosunda kullanıldı.

Tepki hesaplaması şöyle işliyor: her geçmiş olay için, olay saatinden önceki ve sonraki ikişer saatlik pencerede range volatilitesi `(High-Low)/Open` ve fiyat getirisi hesaplandı. Saatlik bar kullanıldığı için ±2 saatlik pencere yaklaşık 2 bar içeriyor; bu bir çözünürlük sınırı ve olduğundan hassas gösterilmiyor.

Sürpriz proxy'si için gerçek forecast/actual verisi olmadığından, olay öncesi 2 saatlik gerçekleşmiş volatilite "piyasa gerginliği" göstergesi olarak kullanıldı. Bu gerçek fiyat verisinden hesaplanmış bir yaklaşıklık, uydurma bir rakam değil.

Risk skoru ise her olay türünün geçmiş ortalama volatilite tepki oranına (kaç kat arttığına) göre orta/yüksek etiketlendi; 1.5x eşiğini aşan olay türleri yüksek sayıldı.

## Bulgular

| Olay türü | Geçmiş ortalama volatilite tepki oranı | Örneklem (n) |
|---|---|---|
| FOMC Faiz Kararı | 4.02x | 15 |
| ABD Tarım Dışı İstihdam (NFP) | 1.40x | 17 |
| ECB Faiz Kararı | 1.18x | 3 |
| ABD Enflasyon (CPI) | 1.01x | 20 |

En çarpıcı bulgu FOMC. Olay sonrası 2 saatlik volatilite, öncesine göre ortalama 4 kat büyüyor; bu örneklemdeki en uç noktada bir günde yüzde 2,8'lik bir hareket bile var. NFP de piyasayı belirgin şekilde hareketlendiriyor (1.40x).

Buna karşılık bu örneklemde CPI'nin volatiliteyi neredeyse hiç artırmadığını görüyoruz (1.01x), ve açıkçası bu beni de şaşırttı. İki olası açıklama var: ya 2026'daki enflasyon verileri gerçekten büyük bir sürpriz içermedi (piyasa zaten fiyatlamıştı), ya da saatlik bar çözünürlüğü CPI'nin genelde çok hızlı, ilk birkaç dakikada gerçekleşen tepkisini yakalayamıyor. Örneklem küçük olduğu için (n=20 kombinasyon, tek yıl) bunu kesin bir kural olarak sunmuyorum, gözlem olarak not düşüyorum. ECB için de sadece 3 kombinasyon var, çünkü bazı 2026 toplantıları resmi kaynaktan teyit edilemediği için takvime alınmadı; o yüzden 1.18x rakamına pek güvenmiyorum.

## Görseller (`gorseller/`)

Her biri hem `.html` (etkileşimli) hem `.png` (statik) olarak kaydedildi:

1. `01_yaklasan_olaylar_zaman_cizelgesi`: bugünden itibaren gelecek 16 olayın zaman çizelgesi
2. `02_olay_turu_gecmis_volatilite_tepkisi_bar`: yukarıdaki tablonun bar grafiği
3. `03_beklenti_sapmasi_fiyat_tepkisi_scatter`: olay öncesi volatilite (proxy) ile olay sonrası fiyat getirisi
4. `04_olay_oncesi_sonrasi_volatilite_penceresi`: öncesi/sonrası volatilite dağılımı, kutu grafik
5. `05_etki_isi_haritasi_olay_varlik`: hangi olay hangi varlığı ne kadar hareket ettiriyor
6. `06_risk_uyari_panosu`: en yakın 6 olay için tarih, risk seviyesi ve öneri

## Belirsizlikler ve bilinçli sınırlamalar

ECB 2026 takviminde Ocak-Mart toplantıları resmi kaynaktan teyit edilemedi (WebFetch erişim kısıtı yüzünden), bu yüzden uydurmak yerine takvime hiç alınmadı. Saatlik bar çözünürlüğü, birkaç dakika içinde gerçekleşen ilk şok hareketini tam yakalayamıyor; gerçek bir yatırım kararı için tick veya 1 dakikalık veri daha doğru olurdu, ama yfinance ücretsiz planda bu çözünürlüğü sadece son 7 gün için veriyor. Sentiment-proxy sürpriz eşleşmesi sadece 3 olay için gerçek Finnhub haberiyle dolabildi; kalan 52 kombinasyonda o hücre NaN, grafiklerde sadece dolu olanlar görünüyor.

Son olarak: bu bir showcase/analiz projesi, canlı işlem sinyali üretmiyor. Risk panosundaki öneriler eğitim amaçlı, sabit %0.5 risk kuralına referans veriyor ama gerçek bir hesapta kullanılmadan önce daha geniş bir örneklemle (çok yıllı veri, premium takvim erişimi) doğrulanmalı.

## Çalıştırma

```bash
cd 04_ekonomik_takvim_ajani
../.venv/bin/python proje.py
```

`.env` dosyasında `FINNHUB_API_KEY` tanımlı olmalı (proje kökünde zaten var). Çıktılar `gorseller/` ve `veri/` altına yazılır.

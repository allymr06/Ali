# JARVIS Tıp Akademisi — 13 Eylül kullanıcı testinin düzeltmesi

Tarih: 14 Eylül 2026. Temel: [TEST_RAPORU.md](TEST_RAPORU.md) (18 bulgu + T19), kod 96e5f41 üzerinde. Bu rapor, bulguların mevcut kodda yeniden üretilmesini, kök nedeni, yapılan düzeltmeyi, düzeltilmiş uygulamada gerçek WebView2 penceresinde yapılan yeniden testi ve veri onarımını kaydeder. Ekran kanıtları `evidence-fix/` altındadır; canlı akışlar `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port` ile açılan gerçek pencerede, sayfanın kendi işleyicileri ve CDP üzerinden sürüldü, taklit sayfa kullanılmadı.

## Ortam

- Test kopyası: `.test-temp/academy-audit-20260913/medical/jarvis_medical.sqlite3` dosyasının SQLite backup API'siyle alınan ayrı bir kopyası (243 belge, 717 soru, 5 sınav, 2 deneme, 7 ustalık satırı). Kullanıcının gerçek veritabanına dokunulmadı; anatomi varlıkları junction ile salt okunur bağlandı.
- Uygulama: gerçek Python köprüsü, gerçek Gemini sağlayıcısı (`gemini-3.5-flash-lite`), `JARVIS_MEDICAL_DIRECTORY` kopyaya yönlendirildi, tek örnek koruması ve tepsi bu çalıştırmada kapalı.
- Sınırlar: fiziksel klavye/fare erişimi bu oturumda reddedildi; Esc, tarayıcı giriş hattına (CDP Input domain) enjekte edildi — testçinin Playwright Esc'iyle aynı katman. Mikrofon ve ses denenmedi. 718 sorunun klinik doğruluğu denetlenmedi.

## Kök neden → düzeltme haritası

| Bulgu | Kök neden (dosya) | Düzeltme |
|---|---|---|
| A01 | `review.scored_eligible` tanımlı ama hiç çağrılmıyor; `from_bank`, `answer`, `finish_exam`, `analyse_attempt` yalnız anahtara bakıyor | Tek karar: `review.rule_decision` / `SourceSupportReviewer.decision`; banka seçici, kâğıt, cevap, bitirme, analiz ve öğrenme kaydı bunu uygular. Puansız sorular *Değerlendirme dışı* bloğunda nedeniyle listelenir; tamamı puansız kâğıtta yüzde yok. Karar bitişte `analysis.scoring`/`policy` ile saklanır (tarihsel politika: kâğıt gününün kararını korur; yalnız "geçersiz say" geriye işler). |
| A03 | `histology.overview` her örneği `reveal=True` gönderiyor; detay isteği de | Açık süreli oturumdaki örnekler yan listede, detayda, kırpma altyazısında (belge adı/sayfa dâhil) ve doğrudan istekte maskeli; ayrıca sayfanın metin katmanından "cevap görselde yazıyor" tespiti, maskeyle gizleme ve oturumun kendi saatiyle geç cevap. |
| A04 | `histology._concept_for` aramanın ilk yakın sonucunu alıyor | Yalnız tam ad/alternatif ad eşleşmesi; eşleşmeyene kararlı `histology.specimen.<slug>` kimliği, adı örnekten okunur. |
| A02 | `.med-doc-panel` max-height, set listesi sınırsız, belge listesi 0'a iniyor | Set listesi kendi sınırında kayar (32vh), belge listesi ≥ 9rem, setler daraltılabilir; dar ekranda 8rem. |
| A05/A08 | `from_bank` belge/sayfa/hoca/görsel süzgeçlerini görmüyor; not/sınav bağlamı sessizce oturumdan geliyor | Süzgeçler gerçekten uygulanır, boş sonuç nedeniyle açıklanır, form taslağı korunur, "Kullanılacak:" satırı üretimden önce etkili bağlamı gösterir, belge başka dersten ise bağlam belgeye göre ayarlanır ve model "kaynak konuyu içermiyor" derse not kaydedilmez. |
| A09 | `runActivity` okuma dalı yalnız kütüphane görünümüne geçiyor | `plan_activity_sources`: plan kapsamındaki belge (sayfa aralığıyla) → konusu eşleşen belgeler → yoksa arama önerisi; eski açık belge kapatılır. |
| A06 | `loadBank` limit 120, profil `slice(0,30)` | Sayfalama (`offset/limit/matched/has_more`), "Daha fazla yükle"; hoca sorularında "30 soru daha göster". |
| A07 | Not/banka kaynakları `span` | Kaynaklar belgeyi ve sayfayı açan düğmeler; silinmiş kaynakta açıklama. |
| A10 | `shell.js` genel Esc ana ekrana dönüyor; `medical.js` yalnız `expanded` sınıfını yakalıyor | Esc önce gerçek tam ekranı ya da genişletilmiş sahneyi kapatır ve orada durur; sahne işleyicisi iki durumu da tanır; çıkış Anatomi Lab'da bırakır. |
| A11 | Lisans metni tek büyük katman | Tek satır + "AYRINTI" ile açılır; tam atıf korunur. |
| A12 | Quiz, işareti olmayan yapıyı "işaretlenen yapı" diye soruyor | `pinned_landmarks` model çapasına bakar; işaretsiz yapı tanımdan sorulur, vurgu yok; quiz sırasında etiketler cevabı yazmaz. |
| A13 | Ham `READY`, `histology`, `anatomy.za_….angulus_inferior` | Türkçe durum etiketleri, ders adları oturum seçeneklerinden, kavram adları lab ve histoloji bankasından çözülür. |
| A14 | Tarih/saat alanları ortak stil dışında | `input[type=date|time]`, çoklu seçim ve tüm `.med-field` alanları temada; `color-scheme`; etiket "Ctrl ile birden çok seç". |
| A15 | Rozetler yalnız tam yenilemeyle | `refreshCounts()` olaylarda; sınav formu taslağı ezilmez. |
| A16 | Başlık `config.question_count` | Başlık ve liste gerçek sayıyı; "M istendi", "K puanlı" ayrı; eski kayıtlar da. |
| A17 | Anlama kontrolü değerlendirme beklerken kesin etiket | `check_payload`: `assessment_status` pending/done/unavailable; "Gerekçe değerlendiriliyor…" ve yeniden dene. |
| A18 | `analyse_attempt` boşları payda sayıyor | Kavram istatistiği yalnız cevaplananlarla; boşlar "Cevaplanmadı" altında ayrı. |
| T19 | Sonuç yalnız kaybolan bir toast'a düşüyordu | `JobLedger`: kimlik, running/done/failed/timeout/interrupted, depoda kalıcı, yeniden dene, mükerrer istek engeli. |

## Bulgu bazında durum ve kanıt

- **A01 — düzeltildi.** Canlı: "Karbonhidrat metabolizması" bankasından 12 soru istendi, 10 hazırlandı, 7 puanlı · 3 yalnız çalışma (liste: "10 soru · 12 istendi · 7 puanlı"). Kaynaksız `q-1beaca7789f4` doğru cevaplandığında cevap yanıtı `scoring.scored=false`, olay yok; ilerlemede "Pentoz fosfat yolu" satırı oluşmadı; sonuç "%14 · 1 doğru · 0 yanlış · 6 boş · 7 puanlı · 3 puansız", *Değerlendirme dışı (3)* bloğu nedenleriyle; iki kez bitirme aynı analiz. `evidence-fix/fix-a01-result.png`.
- **A02 — düzeltildi.** "Histoloji 3 - Epitel" araması: `#med-doc-list` clientHeight 128 (önce 0), satır `elementFromPoint` ile kendi üstünde, tıklama belgeyi açtı; set listesi 251/1161 px kendi içinde kayıyor, "Daralt" çalışıyor. Çözünürlük/ölçek: 1920×1080 ve 1366×768'in %100/%125/%150'de bıraktığı CSS görüş alanlarında (1920, 1536, 1280, 1366, 1094, 911 px) sonuç satırı görünür ve tıklanabilir, liste ≥ 122 px, "Belge ekle" ve "Sınav hazırla" örtülmüyor. `evidence-fix/a02-library.png`, `a02-911x512-1.5.png`.
- **A03 — düzeltildi.** Süreli oturumda yan liste "Sınavdaki örnek · adı gizli", detayda ad/belge/sayfa yok (`detail_leaks=false`), yan satıra tıklamak oturumu bozmuyor ve sızdırmıyor, doğrudan `histology_specimen` isteği `masked=true` ve adsız. Sayfa 34 örneği için metin katmanı denetimi: ad kırpma içinde yazmıyor (`answer_visible=false`) → uygun; yazsaydı *Yalnız çalışma* + "Görseldeki adı gizle". `evidence-fix/fix-a03-timed-hidden.png`, `fix-a03-after-session.png`.
- **A04 — düzeltildi + veri onarıldı.** Cevap `histology.specimen.tek_katli_kubik_epitel` altında; ilerlemede "Tek katlı kübik epitel 3/3", "Tek katlı yassı epitel" satırı yok. Onarım: `ev-6c32c1b6d121` `histology.simple_squamous` → `histology.specimen.tek_katli_kubik_epitel`. `evidence-fix/fix-a13-progress.png`.
- **A05 — düzeltildi.** Biyokimya oturumunda Epitel belgesi s. 34–34 + "Bankadan derle": iş *Başarısız* — "Bankadaki 10 soru Biyokimya › Karbonhidrat metabolizması kapsamında ama süzgece uymuyor (Histoloji 3 - Epitel Doku s. 34-34). Belge/sayfa aralığını ya da hoca seçimini kaldır." Çelişkili filtreyle Cori döngüsü gelmedi. Şık sayısı/bilgi önceliği bankada uygulanmadığı formda yazılı. `evidence-fix/fix-t19-jobs-and-list.png`.
- **A06 — düzeltildi.** Banka "120 / 716 gösteriliyor · toplam 718" + "Daha fazla yükle"; hoca profilinde 30 + "30 soru daha göster". `evidence-fix/fix-a06-bank.png`.
- **A07 — düzeltildi.** Not ve banka kaynakları `data-source` düğmesi; `openSource` silinmiş belgede "Kaynak belge bulunamadı" diyor.
- **A08 — düzeltildi.** "Kullanılacak: Histoloji › konu seçilmedi · Histoloji 3 - Epitel Doku s. 34–34 — Seçilen belge Histoloji dersinden; bağlam belgeye göre ayarlandı (Biyokimya › … yerine). [Oturumu belgeye göre ayarla]"; düğme oturumu Histoloji'ye taşıdı. Kapsam uyuşmazlığında not kaydı birim testle (`source_covers_topic=false` → hata, not yok) sabitlendi. `evidence-fix/a05-note-context.png`.
- **A09 — düzeltildi.** "Düzlemler ve eksenler" için kütüphanede eşleşen belge yok: eski Epitel belgesi kapatıldı, arama "Düzlemler ve eksenler" ile dolduruldu ve seçim istendi; kapsam belgesi olan konularda belge ve sayfa doğrudan açılır (birim test). `evidence-fix/a09-reading.png`.
- **A10 — düzeltildi (fiziksel klavye açık).** Gerçek tam ekran (güvenilir CDP tıklaması): `fullscreenElement` sahne; giriş hattından Esc → tam ekran kapandı, ekran `medical`/`anatomy`, "Scapula · sağ" seçili, Quiz düğmesi `elementFromPoint` ile tıklanabilir (ölü katman yok); genişletilmiş yedek yol da Esc ile kapanıyor. Fiziksel klavye bu oturumda denenemedi. `evidence-fix/a10-fullscreen.png`, `a10-after-escape.png`.
- **A11 — düzeltildi.** Lisans satırı 25 px tek satır, tıklayınca 111 px ayrıntı; kanvas 348 px açık. `evidence-fix/fix-a12-quiz-no-pin.png`.
- **A12 — düzeltildi.** Sağ scapula (17 ders işareti, 0 model çapası): "Bu modelde bu yapı için işaret yok: soru tanımdan sorulur…", stem tanımdan, vurgu yok; zilli sınav 0 istasyonla reddediyor; pinli nörokranyum akışı değişmedi.
- **A13 — düzeltildi.** Sınav durumu "Hazır/Sürüyor/Tamamlandı", ilerlemede "Scapula · sağ · Angulus inferior", "Tek katlı kübik epitel". `evidence-fix/fix-a13-progress.png`.
- **A14 — düzeltildi.** Tarih/saat/çoklu seçim: `color-scheme: dark`, metin/zemin kontrastı 16.53:1, odak halkası görünür. `evidence-fix/fix-a14-plan-form.png`.
- **A15 — düzeltildi.** Sınav rozeti 7 → 8 hazırlanınca; sınav formu taslağı liste yenilenince korundu (belge, sayfa, adet, şık).
- **A16 — düzeltildi.** Eski "10 soru" kaydı listede "8 soru · 10 istendi · 7 puanlı".
- **A17 — düzeltildi.** Gerçek modelle: cevaptan hemen sonra `assessment_status=pending`, ekranda "Gerekçe değerlendiriliyor…"; model bitince "Yanlış cevap, yüksek güven" + not. `evidence-fix/a01b-understanding.png`.
- **A18 — düzeltildi.** 7 boş soruyla sonuçta zayıf kavram yok; "Cevaplanmadı" bloğunda "Şeker fosfatları · 4 boş" vb.
- **T19 — kök neden doğrulandı, düzeltildi.** Histoloji/Epitel s. 34'ten 1 soruluk model sınavı: iş 11 sn'de *Başarısız* — "Üretilen soruların hiçbiri kaynak desteği süzgecinden geçmedi; 1 soru kaynak desteği doğrulanamadığı için kâğıda alınmadı (Çelişkili kanıt×1); soru bankasında durumuyla duruyor." İkinci tıklama "Bu sınav zaten hazırlanıyor" ile reddedildi; iş satırı sayfa yeniden yüklemesinden ve uygulama yeniden başlatmasından sonra da nedeni ve "Yeniden dene" ile duruyor. Testçinin gördüğü sessizlik, kaybolan bir toast'tı.

## Veri onarımı

`scripts/repair_medical_learning.py` test kopyasında: yedek alındı (`backups/jarvis_medical-before-repair-*.sqlite3`), `q-1beaca7789f4` puansız → `biochemistry.pentose_phosphate` satırı kaldırıldı, olayı `excluded` işaretlendi (silinmedi); histoloji olayı doğru kavrama taşındı; 2 sınav sonucu yeniden hesaplandı (3 soruluk sınav: %33 → 2 puanlı, %0 · 1 puansız doğru). İkinci çalıştırma: 0 değişiklik, "daha önce uygulanmış". Gerçek kullanıcı veritabanına uygulanmadı; komut: `.venv\Scripts\python.exe scripts\repair_medical_learning.py --dry-run` ile önce deneme.

## Kalıcılık

Uygulama kapatılıp açıldıktan sonra: 8 sınav doğru sayılarla, 6 deneme, başarısız işler nedenleriyle, örnek/kavram/oturumlar ve okunur ilerleme adları yerinde.

## Testler

Yeni/uyarlanmış: `tests/test_medical_scoring.py` (24 test), `tests/test_nova_web.py` (+6 sayfa testi); mevcut 22 test, yeni sözleşmeye göre güncellendi (banka fixture'ları kişi anahtarlı, başlık/not beklentileri). Koşulan paketler ve toplamlar bu raporun sonundaki satırda.

## Değişen dosyalar

`app/medical/{review,generation,questions,academy,study,histology,documents,learning,understanding,planner,prompts,schemas,anatomy,models,store}.py`, yeni `app/medical/{jobs,repair}.py`, `scripts/repair_medical_learning.py`, `app/ui/nova/shell.py`, `app/ui/nova/web/{index.html, css/medical.css, js/medical.js, js/study.js, js/shell.js}`, testler ve belgeler (`docs/MEDICAL_ACADEMY.md`, `docs/TESTING.md`, `docs/PROJECT_STATE.md`).

Test toplamı: `scripts/verify.py` — 2500 test geçti, 4 atlandı (bağımlılık bütünlüğü ve derleme kapıları dâhil). Hedefli paketler (tests/test_medical*.py, test_atlas_catalog.py, test_nova_web.py, test_ui_nova.py): 1155 test.

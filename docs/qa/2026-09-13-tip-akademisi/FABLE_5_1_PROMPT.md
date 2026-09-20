# Fable 5.1 için JARVIS Tıp Akademisi düzeltme promptu

JARVIS projesinin Tıp Akademisi bölümünü baştan sona çalışır, tutarlı ve ölçme açısından güvenilir hale getir. Yalnız öneri veya plan yazma: aşağıdaki doğrulanmış hataları kodda düzelt, eksik akışları tamamla, gerçek uygulamada tekrar dene ve sonuçları kanıtla. Mevcut özellikleri kaldırarak, hatalı sonuçları gizleyerek veya sahte başarı verisi göstererek işi tamamlanmış sayma.

Proje: C:\Users\MeGaComputers\JARVIS

Önce depodaki geçerli çalışma talimatlarını ve docs/qa/2026-09-13-tip-akademisi/TEST_RAPORU.md dosyasını oku. İlgili ekran görüntüleri, arayüz metinleri ve test raporu aynı dizinin evidence altındadır. Rapor 13 Eylül 2026 tarihli, 96e5f41 kodu üzerinde yapılan gerçek Windows pywebview/WebView2 kullanıcı testine dayanır. Güncel kod değişmişse satır numaralarına bağlı kalma; davranış ve işlev adlarıyla doğrula.

Başlangıç verisinde 243 belge, 716 soru vardı. Test kopyası .test-temp/academy-audit-20260913 içinde bulunabilir. Gerçek kullanıcının veritabanı yerine SQLite backup ile aldığın ayrı bir kopyada yazma işlemlerini ve testleri yap. Bu eski test dizini bulunmuyorsa küçük, temsilî ve tekrar üretilebilir test verisi oluştur. Ayarları, anahtarları veya kullanıcı veritabanını çıktılarına koyma. Kullanıcının mevcut değişikliklerini koru. Gereksiz framework değişimi ve yeniden yazım yapma.

Tıp Akademisi kapsamı: Panel, Plan, Konular, Kütüphane, Notlar, Sınav, Soru Bankası, Anlama, Histoloji, Hoca Tarzı, İlerleme ve Anatomi Lab. Düzeltmelerin bu ekranlar arasındaki bağlam, kaynak, durum ve öğrenme ilişkilerini birlikte ele almasını istiyorum.

## Öncelikli düzeltmeler ve kabul koşulları

### 1. A01 — Puansız soruların puan ve öğrenmeyi kirletmesini durdur

Somut hata: q-1beaca7789f4 bankada “KAYNAKSIZ (YALNIZ ÇALIŞMA) · PUANSIZ” iken 3 soruluk sınavda doğru sayıldı; diğer 2 soru boşken %33 üretildi ve “Pentoz fosfat yolu 1/1” ilerlemesi yazıldı.

app/medical/review.py scored_eligible, generation.py from_bank, questions.py grade/analyse_attempt, academy.py answer/finish_exam ve ilgili anlama/öğrenme yollarını incele. Tek bir puanlanabilirlik kararı tanımla ve üretim, bankadan seçim, sınava sunum, cevap kabulü, bitirme, yeniden hesaplama ve öğrenme kayıtlarında tutarlı uygula. Sadece cevap anahtarının varlığı yeterli sayılmasın.

Kabul: Kaynaksız/yalnız çalışma, çelişkili, geçersiz ve politikaya göre artık uygun olmayan soru çalışma modunda neden açıklamasıyla görülebilir; puanlanabilir ölçüme sessizce katılamaz. Pay/payda ve öğrenmeye etkisi aynı kurala uyar. Tamamı puansız oturumda yanıltıcı %0 veya %100 yerine değerlendirme dışı durum gösterilir. Çalışma etkinliği ayrı kaydedilebilir ama başarı/ustalık kanıtı sayılmaz. Eski sınavı açmak veya bitirmeyi iki kez çağırmak kayıt çoğaltmaz. Sonradan destek durumu değişen soruların tarihsel değerlendirme politikası açık ve izlenebilir olsun. Mevcut yanlış türetilmiş özetleri kaynak olaylardan, yedekli ve tekrar çalıştırılabilir biçimde düzelt; geçmiş cevapları silme.

### 2. A03–A04 — Histoloji sınavını ve kavram eşlemesini düzelt

Somut hata: “Histoloji 3 - Epitel Doku” (doc-b3b2c224a737), sayfa 34'ten oluşturulan “Tek katlı kübik epitel” örneği süreli sınavda sol listede doğru etiketi açık bıraktı. Doğru cevap ilerlemede “Tek katlı yassı epitel 1/1” oldu.

study.js renderSpecimens/renderSession ve histology.py _concept_for yollarını düzelt.

Kabul: Süreli/kör sınav sırasında yan liste, önizleme, kaynak açıklaması, title/alt/erişilebilir ad ve ayrıntı paneli cevabı açığa çıkarmaz. Çalışma ve sonuç inceleme modunda öğretici etiketler geri gelir. Görselin üzerindeki cevabı içeren bölgeler için maskeleme veya sınava uygunluk mekanizması olsun; görselde yazan cevap gizlenemiyorsa bunu puanlı kör sınava uygun sayma. Süre dolması, son anda cevap ve tekrar gönderme tek sonuç üretir.

Kavram eşlemesi yalnız doğrulanmış tam ad/alternatif ad veya açık katalog ilişkisiyle yapılır. Aramanın ilk yakın sonucunu öğrenme kimliği sayma. Eşleşmeyen örneğe kararlı ayrı kimlik ver. “Tek katlı kübik epitel” cevabı “Tek katlı yassı epitel” ustalığını değiştiremez. Mevcut yanlış bağlantıları kaynağı belli bir veri düzeltmesiyle onar; tıbbi eşdeğerlik uydurma.

### 3. A02 — Kütüphane listesini her zaman erişilebilir tut

Somut hata: 243 belgeli, uzun komite listeli veride “Histoloji 3 - Epitel” araması 1 sonuç buluyor fakat #med-doc-list clientHeight=0 oluyor; sonuç tıklanamıyor. medical.css içindeki med-doc-panel, med-list ve med-set-list boyutlandırmasını düzelt.

Kabul: Ders setleri açılıp kapatılabilir/kaydırılabilir; arama ve sonuçlar sıfır yüksekliğe inmez. Fare, klavye ve odak görünürlüğü çalışır. 1920×1080 ve 1366×768 ile %100, %125, %150 Windows ölçeklerinde hiçbir kaplama sonuçları veya temel eylemleri kesmez. Uzun belge isimleri ve boş sonuç da kullanılabilir kalır. Yalnız z-index artırarak alttaki yerleşim sorununu saklama.

### 4. A05, A08, A09 — Ders/konu/belge/sayfa bağlamını tutarlı taşı

Somut örnekler:

- Bankadan derle: 1 soru, 4 şık, Epitel Doku sayfa 34–34 seçiliyken 5 şıklı Cori döngüsü sorusu geldi.
- Biyokimya/Karbonhidrat oturumunda epitel sayfasından üretilen not, “kaynakta karbonhidrat yok” uyarısını normal biyokimya notu olarak kaydetti.
- Anatomi planındaki “Düzlemler ve eksenler” okuma etkinliği eski histoloji araması ve açık epitel sayfasına götürdü.

medical.js examConfig/createExam/createNote, belge kısayolları, form render işlemleri, study.js runActivity ve generation.py from_bank üzerinde etkili bağlam sözleşmesini düzenle.

Kabul: Kullanıcı üretimden önce hangi ders, konu, belge ve sayfa aralığının kullanılacağını görür. Çelişkili seçim sessizce başka konuya genişlemez; ilgili bağlam güncellenir veya açık seçim sunulur. Banka modunda desteklenen filtreler gerçekten uygulanır; uygulanamayan ayar baştan açıklanır/devre dışıdır. Şık sayısını tutturmak için özgün sorudan şık silme. Yanlışlarım, hoca, görsel, zorluk ve zayıf konu seçeneklerini birbirleriyle birlikte doğrula. Uygun soru yoksa dürüst ve eyleme dönük boş durum göster.

Belgeden not/sınav kısayolu doğru belge ve sayfayı taşır; asenkron liste yenilemesi yeni seçimi ezmez. İçeriksiz veya kapsam uyuşmazlığı cevabı “not hazır” diye kaydedilmez. Plandaki okuma, ilgili kaynağa gider; eşleştirme yoksa ilgili kaynak seçimi sunar. Başlatmak tek başına tamamlandı veya öğrenildi anlamına gelmez.

### 5. A06–A07 — Bütün soru ve kaynaklara erişim sağla

Banka 716 soruda ilk 120 ile, Burcu Baba profili 59 soruda ilk 30 ile sınırlı kalıyor. medical.js loadBank ve profile.questions.slice(0,30) yollarında gerçek sayfalama/daha fazla yükleme ekle. Profil belge kesmelerini de denetle.

Kabul: Kullanıcı bütün sonuçlara ulaşabilir; filtreli toplam ile genel toplam ayırt edilir; sayfa geçişinde atlanan/tekrarlanan kayıt olmaz; büyük veride ekran donmaz. Arama/filtre değişince sayfalama tutarlı sıfırlanır.

Not ve bankadaki “s.34” gibi kaynak işaretlerini doğru belge/sayfayı açan erişilebilir düğmelere dönüştür. Aynı sayfa numarasına sahip farklı belgeler karışmasın. Kaynak yoksa veya silinmişse açıklama göster. Kaynak metni ile standart bilgi katkısını ayırt et. Şekle bağlı soruların banka, hoca profili, sınav ve sonuç görünümünde görsellerini ayrıca doğrula; görsel eksikse soruyu çözülebilir gibi sunma.

### 6. A10–A12 — Anatomi Lab'ın ekran ve sınav davranışını tamamla

Tam ekran bulgusu CDP sentetik Escape ile üretildi: fullscreenElement açık kalırken shell.js ana ekrana döndü; medical.js yalnız expanded sınıfına bakıyor. Önce fiziksel klavyeyle WebView2'de tekrarla; sentetik tuş farkını kesin kullanıcı hatası gibi raporlama. Genel Escape yönlendirmesi gerçek Fullscreen API ve CSS yedeğiyle çakışmayacak şekilde tasarlansın.

Kabul: Escape öncelikle açık iletişim kutusunu veya tam ekranı uygun sırada kapatır. Tam ekrandan çıkış kullanıcıyı Anatomi Lab'da, aynı modelde bırakır; görünmez katman veya tıklama kilidi kalmaz. Hem native fullscreen hem yedek genişletme yolu test edilir.

Sağ scapulada kaynak/lisans metni modeli örtüyor. Zorunlu atıfları koruyarak kısa satır + açılabilir ayrıntı düzenle; modelin ve sınav hedefinin üstünü kapatma.

Pinsiz scapula modelinde “işaretlenen yapı” sınavı başlıyor ama işaret yok. Model yeteneğine göre işaretli sınavı başlat veya açık metin tabanlı mod sun. Eksik anatomik koordinatı tahmin ederek pin üretme. Pinsiz modelde zilli sınavın mevcut açıklamasını koru; pinli nörokranyumda 10 istasyon akışını, süreyi, erken bitirmeyi ve sonuç kaydını doğrula. Etiket açma/kapama veya izolasyon, sınav cevabını ele vermesin veya hedefi kaybettirmesin.

### 7. A13–A18 — Sonuçları ve ekran durumunu doğru ifade et

- READY, histology/biology ve anatomy.za_cdbfb237d6bba5c9.angulus_inferior gibi sistem değerlerini kullanıcıya uygun Türkçe durum/ders ve okunur kavram/model adıyla göster. Geçerli Latince anatomi adlarını koru; olmayan karşılık uydurma.
- Plan tarih/saat alanlarına koyu temaya uygun metin, ikon ve color-scheme ekle. Odak belirgin olsun; çoklu ders seçimi anlaşılır olsun. Kontrastı ölçerek doğrula.
- Başarılı oluşturma/silme/güncelleme olayları sekme rozetleri, listeler ve Panel özetini birlikte güncellesin. 5 sınav varken 1 gösterilmesin. Bunun için form taslağını silen tam yenileme kullanma; eski asenkron yanıt yeni seçimi ezmesin.
- Başlıkta “10 soru” deyip içerikte 8 soru göstermeyi düzelt. İstenen, kabul edilen ve puanlanabilir adetler gerektiğinde ayrı görünür; eleme nedenleri açıklanır. Eski kayıtları da doğru göster.
- Anlama değerlendirmesi sürerken “gerekçe yok ya da tahmin” gibi kesin karar verme. Bekliyor, değerlendiriliyor, tamamlandı ve hata durumları ayrı olsun; gönderilmiş gerekçeyi ve güven seçimini koru.
- 1 doğru, 0 yanlış, 2 boş sınavda boşları yanlış kavram kanıtına dönüştürme. Boş, yanlış, doğru, puansız ve henüz değerlendirilmemiş durumları ayır. “Zayıf kavram” çıkarımı cevaplanmış uygun kanıta ve tanımlı yeterlilik eşiğine dayansın. Sınav puanında boşun etkisi ile kavram ustalığı çıkarımı ayrı kurallardır.

### 8. T19 — Arka plan üretiminin sonucunu görünür ve güvenilir yap

Son kullanıcı testinde Histoloji/Epitel, sayfa 34, 1 soruluk modelden sınav isteği “Sınav hazırlanıyor” dedi; son gözlem/yeniden yüklemede yeni sınav veya açık hata görülmedi. Bunun kök nedeni henüz doğrulanmadı. Önce işin kabulü, sağlayıcı çağrısı, hata yakalama, olay iletimi ve kayıt yazımını izle; tahmin ederek düzeltme yapma.

Kabul: Başlayan işe kararlı kimlik ve görünür durum ver. Hazırlanıyor ekranı eski sınavı yeni sonuç gibi sunmaz. Başarı, başarısızlık, zaman aşımı ve varsa iptal terminal durumları görünürdür. Aynı isteğe art arda tıklama kontrolsüz mükerrer iş üretmez. Sayfa değişimi/yeniden yükleme sonucu kaybettirmez. Yeniden deneme kullanıcı girdisini korur; yarım iş sahte başarı kaydı bırakmaz. Sağlayıcı yoksa açık hata göster; başarılı test yapmış gibi anlatma.

## Uygulama ve doğrulama yöntemi

1. Önce bulguları mevcut kodda yeniden üret ve kısa hata–kök neden–dosya eşlemesi çıkar. Ardından puan/öğrenme, erişim engelleri, bağlam, sonuç ve arayüz sırasıyla düzelt. Genel refaktöre yayılma.
2. Veri davranışını etkileyen düzeltmeler için anlamlı regresyon testleri ekle: puansız/destek durumu, tekrar bitirme, yanlış kavram eşleşmesi, kapsam filtreleri, boş/yanlış ayrımı, asenkron durum yarışları ve sayfalama. Sırf uygulamayı tekrar eden testler yazma. Eski testleri hatalı davranışı kabul edecek şekilde gevşetme.
3. Mevcut ilgili test paketi kullanıcı testi sırasında 1125 testle geçti. Bu, hataların yokluğu anlamına gelmedi. tests/test_medical*.py ile tests/test_atlas_catalog.py, tests/test_nova_web.py ve tests/test_ui_nova.py paketlerini yeniden çalıştır; değişikliğin etkilediği diğer kontrolleri de çalıştır. Çalıştırmadığın kontrolü geçti diye yazma.
4. Gerçek Python köprüsüne bağlı Windows uygulamasında 12 sekmeyi tekrar gez. Plan → ilgili okuma → not → kaynak → sınav → cevap → sonuç → anlama → ilerleme zincirini çalıştır. Histoloji ve anatomi için hem çalışma hem sınav modunu dene. Yalnız ekranların açılmasını uçtan uca test sayma.
5. Kayıtları uygulamayı kapatıp açtıktan sonra doğrula. Boş veri, büyük veri, uzun Türkçe başlık, eksik kaynak, görsel eksikliği, geçersiz sayfa/tarih, sağlayıcı kesintisi, süre bitimi ve hızlı tekrarlı tıklama durumlarını kapsa. İçe aktarmayı temsilî PDF/görsel/metin verisiyle test et; mevcut gerçek belge arşivini topluca yeniden işleme.
6. Kaynak gösterimi ve şekle bağlı soru bütünlüğünü denetle. Tıbbi içerik değişikliği gerekiyorsa güvenilir birincil kaynak ve ilgili ders sayfasıyla doğrula; modelin kendi cevabını tek doğrulama kabul etme. 716 sorunun tamamını klinik açıdan denetlemediysen bunu açıkça belirt.
7. Çıktı olarak değişen dosyaları, giderilen A01–A18 maddelerini, T19'un doğrulanmış sonucunu, yapılan veri düzeltmelerini, test sayısını ve gerçek ekran kanıtlarını sun. Her madde için “düzeltildi / yeniden üretilemedi / engelli” durumunu kanıtla. Fiziksel Esc, ses/mikrofon veya başka ortam gereksinimi eksikse onu açık bırak; genel “her şey tamam” sonucu üretme.

İşin bitti sayılması için: puansız sorular ustalık/puan üretmemeli; histoloji cevabı sınav sırasında görünmemeli ve doğru kavrama yazılmalı; kütüphane sonuçlarına erişilmeli; kaynak ve kapsam seçimleri korunmalı; bütün banka sorularına ulaşılmalı; sonuçlar ve sayaçlar tutarlı olmalı; Anatomi Lab modeli/etiketi/işareti ile sınav metni çelişmemeli. Bunları düzeltip test ederek teslim et.

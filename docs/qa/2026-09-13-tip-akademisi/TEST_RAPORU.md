# JARVIS Tıp Akademisi — kullanıcı testi

Tarih: 13 Eylül 2026. İncelenen kod: 96e5f41. Bu çalışma uygulamayı düzeltmez; kullanıcı testinin sonuçlarını ve düzeltme gereksinimlerini kaydeder.

## Sonuç

12 sekmenin tamamına girildi ve temel çalışma akışları gerçek masaüstü uygulamasında denendi. 18 bulgu kaydedildi. Bunlardan tam ekran/Esc bulgusunun fiziksel klavyeyle ayrıca doğrulanması gerekiyor. Son modelden sınav üretimi denemesi sonuçlanmadığı için ayrı bir açık test olarak bırakıldı. Bütün olası hataların bulunduğu veya bütün tıbbi içeriğin doğrulandığı iddia edilmiyor.

En önemli sorunlar: puansız soruların başarı ve öğrenme hesabına katılması, histoloji sınavında cevabın görünmesi, histoloji sonucunun yanlış kavrama yazılması ve kütüphane sonuç listesinin tıklanamaz duruma gelmesi.

## Ortam ve yöntem

- Windows, Python 3.12, pywebview/WebView2. Uygulamanın kendi Python köprüsüne bağlı gerçek arayüzü kullanıldı; taklit sayfa kullanılmadı.
- Veri başlangıcı: 243 belge, 716 soru, 1 sınav. SQLite yedeği ayrı test dizinine alındı. Yeni plan, not, sınav, örnek, cevap ve ilerleme kayıtları bu kopyaya yazıldı. Mevcut anatomi varlıkları ve belge dosyaları okunarak kullanıldı.
- Test durumu: .test-temp/academy-audit-20260913. Bu dizindeki veritabanı ve ayar dosyaları rapor paketine dahil edilmedi.
- Etkileşimler gerçek WebView2 oturumunda Playwright/CDP üzerinden yapıldı. Yerel pencere otomasyonunda odak/koordinat sorunları bulunduğundan fiziksel fare ve klavye davranışının her ayrıntısı sertifikalandırılmadı.
- Kanıtlar bu raporun yanındaki evidence dizininde PNG, arayüz metni ve JSON olarak bulunur. Dosya numaraları akış sırasıdır; ardışık olmaları gerekmez.
- İlgili otomatik kontroller: tests/test_medical*.py, tests/test_atlas_catalog.py, tests/test_nova_web.py, tests/test_ui_nova.py. Sonuç: **1125 geçti, 0 başarısız; 159,40 saniye**. Bu sayı bütün deponun test toplamı değildir. JUnit: evidence/pytest.xml.
- Dinlenen tarayıcı pageerror olayları boştu. Bu, bütün ağ, model, Python veya mantık hatalarının olmadığı anlamına gelmez.

## Kapsam

| Sekme | Denenen akış |
|---|---|
| Panel | Özetler, gezinme ve sekme sayaçları |
| Plan | Boş/geçmiş tarih kontrolü, plan oluşturma, kapasite uyarısı, okuma etkinliğini başlatma |
| Konular | Yedi dersin konu ağacını açma ve konu seçme |
| Kütüphane | Arama, belge açma, sayfaya gitme, kaynak sayfası, histoloji kırpma, sesli anlatımı hazırlama/duraklatma/durdurma |
| Notlar | Seçili belgenin sayfasından modelle not oluşturma ve kaydını açma |
| Sınav | Bankadan derleme, soru/şık/sayfa ayarları, cevap, gezinme, boş soruyla bitirme onayı, sonuç, yeniden yükleme |
| Soru Bankası | Arama/filtreler, destek durumu, soru işaretleme ve hata bildirimi |
| Anlama | Güven düzeyi, cevap ve gerekçe, değerlendirme, sonucu yeniden açma |
| Histoloji | Sayfadan örnek kaydı, süreli tanıma, cevap, sonuç, ilerleme ilişkisi |
| Hoca Tarzı | Profil açma ve kaynak soru listesi |
| İlerleme | Sınav, histoloji ve anatomi sonrası kayıtları karşılaştırma |
| Anatomi Lab | Model/sahne açma, kamera, izolasyon kontrolü, tam ekran, kısa sınav ve zilli sınav |

Çalışan örnekler: planın kapasite aşımını belirtmesi; geçmiş tarihi reddetmesi; sınavda iki boş soru için bitirme onayı; cevap ve sonucun saklanması; kaynaklı histoloji kırpmasının kaydedilmesi; pinli nörokranyum sahnesinde zilli sınavın başlaması ve bitmesi; pinsiz modelde zilli sınavın açıklamayla reddedilmesi; anlatımın hazırlanıp duraklatılması ve durdurulması. Sesin işitsel kalitesi ayrıca dinlenmedi.

## Bulgular

P1: ölçme/öğrenme doğruluğunu bozan veya ana akışı engelleyen sorun. P2: yanlış yönlendirme, erişim, durum veya kullanılabilirlik sorunu.

### A01 — P1 — Puansız soru puana ve öğrenme kaydına giriyor

Soru Bankası'nda q-1beaca7789f4, “KAYNAKSIZ (YALNIZ ÇALIŞMA) · PUANSIZ” olarak gösterildi. Üç soruluk bankadan derlenmiş sınavda bu pentoz fosfat yolağı sorusu doğru cevaplandı, diğer ikisi boş bırakıldı. Sonuç %33 ve 1 doğru oldu. İlerleme sayfası “Pentoz fosfat yolu 1/1” yazdı.

Beklenen: çalışma amacıyla gösterilen puansız soru, puan payı/paydasını ve değerlendirmeye dayanan öğrenme istatistiklerini etkilememeli; kullanıcı puanlanabilir soru sayısını ve hariç bırakma nedenini görebilmeli.

Kod izi: app/medical/review.py içindeki scored_eligible tanımlı fakat app/medical içinde çağrısı bulunmadı. generation.py:402 from_bank, questions.py:474 analyse_attempt, academy.py:1479 answer ve :1554 finish_exam yolları birlikte incelenmeli. Yalnız cevap anahtarı bulunması, kaynak/destek uygunluğu kontrolünün yerini tutmamalı.

Kanıt: 25-unscored-bank-proof, 07-exam-results, 26-progress-unscored.

### A02 — P1 — Kütüphane arama sonucu görünür/tıklanabilir alanını kaybediyor

243 belgeli veride “Histoloji 3 - Epitel” araması 1/243 sonuç verdi. Ders setlerinin açık komite listeleri panelin yüksekliğini tüketti. Sonuç satırına tıklama başka katman tarafından kesildi. #med-doc-list ölçümü clientHeight=0, scrollHeight=69 oldu. Klavyede Tab ve Enter ile belge açılabildi; bu bir geçici yol, normal erişim değil.

Beklenen: uzun ders setlerinde de arama sonuçları görünür, kaydırılabilir ve tıklanabilir kalmalı.

Kod izi: app/ui/nova/web/css/medical.css:86–87 ve :135. Panel max-height ile sınırlı; set listesi sınırlandırılmıyor ve belge listesi sıfıra küçülebiliyor.

Kanıt: 10-library-click-blocked, 11-document-keyboard-open.

### A03 — P1 — Histoloji süreli sınavında cevap yan panelde açık

“Histoloji 3 - Epitel Doku”, sayfa 34'ten “Tek katlı kübik epitel” etiketli örnek oluşturuldu. Bir soruluk 15 saniyelik sınav başlatıldı. Ana alan “Bu nedir?” diye sorarken sol örnek listesinde aynı kaynağın doğru etiketi görünüyordu. Etiket yazılarak %100 alındı.

Beklenen: sınav sırasında aynı örneğin etiketi, başlığı, açıklaması ve erişilebilir metinleri cevabı vermemeli. Çalışma modunda açıklamalar korunmalı. Görselde zaten yazan cevaba karşı maskeleme/uygunluk kuralı da tanımlanmalı; bütün örnekler kendiliğinden kör sınava uygun sayılmamalı.

Kod izi: app/ui/nova/web/js/study.js renderSpecimens, renderSession ve örnek gösterimi.

Kanıt: 14-histology-created, 15-histology-timed, 16-histology-answer.

### A04 — P1 — Kübik epitel cevabı yassı epitel öğrenmesine yazılıyor

A03'te doğru cevaplanan “Tek katlı kübik epitel”, İlerleme ve konu ekranında “Tek katlı yassı epitel 1/1” olarak kaydedildi. İki farklı etiketin aynı kavram sayılmasına somut örnek.

Kod izi: app/medical/histology.py:115 _concept_for, arama sonucundaki ilk histology kavramını eşleşmenin doğruluğunu kontrol etmeden kabul ediyor.

Beklenen: doğrulanmış tam ad/alternatif ad eşleşmesi; bulunamazsa kararlı ayrı örnek kavramı. Yakın metin araması öğrenme kimliğini sessizce değiştirmemeli. Mevcut yanlış ilişkilerin düzeltmesi yedekli ve izlenebilir olmalı.

Kanıt: 16-histology-answer, 26-progress-unscored, 24-seven-subjects.json.

### A05 — P2 — Bankadan derleme belge, sayfa ve şık ayarlarını yok sayıyor

Biyokimya/karbonhidrat oturumunda form 1 soru, 4 şık, Histoloji 3 - Epitel Doku, sayfa 34–34 olarak ayarlandı. “Bankadan derle” 5 şıklı Cori döngüsü sorusu getirdi; seçilen histoloji sayfasına bağlı değildi.

Beklenen: desteklenen filtrelere uyan sonuç; çelişkili filtrelerde açıklamalı boş sonuç. Banka modunda uygulanamayan seçenekler açıkça devre dışı veya açıklamalı olmalı. Orijinal sorunun şıklarını keserek sayıyı tutturmak kabul edilmez.

Kod izi: generation.py:402 from_bank, medical.js examConfig/createExam. Belge/sayfa/şık dışında hoca, yanlışlarım ve görsel kısıtlarının aynı sözleşmeyi izlemesi ayrıca test edilmeli; bunların tümünün bozuk olduğu bu testte iddia edilmiyor.

Kanıt: 23-bank-ignored-filters.

### A06 — P2 — Banka ve hoca sorularının kalanına erişim yok

Banka 716 toplam soruya karşılık ilk 120'yi getiriyor; sonraki sayfaya erişim bulunamadı. Burcu Baba profilinde 59 soru belirtilirken ilk 30 gösteriliyor.

Kod izi: medical.js:1266 loadBank içindeki limit ve :1476 profile.questions.slice(0,30). Profilde belge listesindeki kesme davranışı da gözden geçirilmeli.

Beklenen: sonraki sayfa/daha fazla, doğru filtreli toplam, kararlı sıralama, tüm kayıtlara ulaşma.

Kanıt: tab-bank, 22-professor-detail.

### A07 — P2 — Not ve banka kaynak işaretleri kaynağı açmıyor

Notun “s.34” kaynak işareti tıklanabilir bağlantı değil. Bankada da kaynaklar span olarak gösteriliyor. Sınav sonucundaki çalışan kaynak düğmeleri bu iki görünümde yok.

Kod izi: medical.js renderNotes ve renderBank. Beklenen: belge kimliği ve sayfası doğru taşınan, klavyeyle de açılabilen kaynak düğmesi; bulunamayan kaynakta dürüst açıklama.

Kanıt: 21-generated-note, tab-bank; DOM/kod incelemesi.

### A08 — P2 — Eski konu bağlamıyla anlamsız not başarıyla kaydediliyor

Oturum Biyokimya/Karbonhidrat iken histoloji belgesi sayfa 34 seçildi. Oluşan “Karbonhidrat Metabolizması” notunun metni, kaynağın karbonhidrat içermediğini ve epitel dokuyla ilgili olduğunu söyledi. Bu uyarı biyokimya notu olarak kaydedildi. Model bu örnekte içerik uydurmadı; hata bağlam yönetimi ve sonuç sınıflandırmasında.

Beklenen: üretimden önce etkili ders/konu/belge/sayfa bağlamını göster; uyuşmazlığı çöz; içeriksiz/uyuşmazlık cevabını normal not sayma. Belgeden gelen kısayollar bağlamı doğru taşımalı; asenkron liste yüklemesi kullanıcının seçimini silmemeli.

Kod izi: medical.js createNote, belge eylemleri ve renderNoteForm/renderExamForm.

Kanıt: 21-generated-note.

### A09 — P2 — Plan okuma etkinliği ilgisiz eski belgeye götürüyor

Anatomi planında “Düzlemler ve eksenler” okuma etkinliğine Başlat denince Kütüphane açıldı; eski histoloji araması ve Epitel Doku sayfa 34 seçimi kaldı.

Beklenen: etkinliğe bağlı kaynak varsa doğru sayfaya git; yoksa ilgili adayları ve kaynak seçme ihtiyacını göster. İlgisiz açık belgeyi etkinliğin kaynağı gibi bırakma.

Kod izi: study.js runActivity, reading dalı yalnız library görünümüne geçiyor. Kanıt: canlı akış gözlemi; plan kaydı 02-plan-created, eski belge 12-histology-page. Geçiş anına özel ekran görüntüsü yok.

### A10 — P1 adayı — Tam ekran Esc ile ana ekrana kaçış / etkileşim kilidi

Sağ scapula modelinde tam ekran açıldı; native fullscreenElement doluydu. CDP üzerinden Escape gönderildiğinde aktif ekran home oldu, fullscreenElement dolu kaldı ve başka düğmelerin tıklanması üst katmanda kesildi. Testi sürdürmek için document.exitFullscreen ile toparlandı.

Kod izi: shell.js:599 genel Escape ana ekrana dönüyor; medical.js:3081 yalnız expanded CSS sınıfını yakalıyor. Gerçek Fullscreen API durumu aynı korumayı kullanmıyor.

Beklenen: Escape önce tam ekranı kapatıp kullanıcıyı Anatomi Lab'da bırakmalı; fiziksel klavye ve WebView2 üzerinde ayrıca tekrar edilmeli. CDP sentetik tuşunun tarayıcının yerel Escape davranışıyla farkı nedeniyle kesin fiziksel kullanıcı hatası olarak sınıflandırılmadı.

Kanıt: 19-after-fullscreen-escape.png, DOM durum ölçümleri ve kod izi.

### A11 — P2 — Anatomi lisans metni model alanını kaplıyor

Sağ scapulada uzun kaynak/lisans metni çalışma alanının alt bölümünde büyük bir katman oluşturdu ve kemiğin alt kısmını örttü.

Beklenen: zorunlu atıf korunarak kısa görünüm ve açılabilir ayrıntı; model, hedef ve sınav kontrolü örtülmemeli. Kanıt: 20-anatomy-quiz.png.

### A12 — P2 — Pinsiz model sınavı “işaretlenen yapı” diyor

Sağ scapula/canonical scapula için kullanılan varlıklarda gerekli işaret konumları yokken kısa sınav başladı ve “üzerinde işaretlenen yapı” dedi. Görünür hedef işareti bulunmadı. Zilli sınav ise pinsiz modelde açıklamayla başlamadı; pinli nörokranyum sahnesinde çalıştı.

Beklenen: model yeteneğine göre gerçek işaretli sınav veya açıkça metin tabanlı soru; koordinat uydurma yok. Kullanılamayan eylemi başlatmadan açıklama. Görünür etiketlerin cevabı ele vermemesi ayrıca denetlenmeli.

Kanıt: 17-anatomy-scapula, 20-anatomy-quiz, 27-bell-ringer.png.

### A13 — P2 — Kullanıcıya ham durum ve kavram kimlikleri çıkıyor

Sınav durumunda READY, bazı ders alanlarında histology/biology, ilerlemede anatomy.za_cdbfb237d6bba5c9.angulus_inferior görüldü.

Beklenen: Türkçe durum/ders adları, okunur anatomi kavramı ve model adı. Geçerli Latince anatomik adları keyfî çevirme veya uydurma yok.

Kanıt: tab-library, 26-progress-unscored, 28-reload-persistence.

### A14 — P2 — Plan tarih/saat alanı koyu temayla uyumsuz

Plan formundaki tarih/saat alanlarında açık zemin ve düşük okunurluk oluştu. CSS'deki ortak alan stilleri date/time türlerini kapsamıyor.

Beklenen: okunur metin/ikon, uygun color-scheme, görünür odak ve açıklayıcı etiket. Çoklu ders seçiminin nasıl kullanılacağı açık olmalı. Sayısal kontrast oranı bu testte ölçülmedi.

Kanıt: 01-plan.png, 02-plan-created.png.

### A15 — P2 — Sekme sayaçları yeni kayıtlarla birlikte güncellenmiyor

Oturum içinde sınav listesi ve veritabanında 5 sınav varken sekme bir süre 1 gösterdi; yeni not/örnek de aynı anda rozetlere yansımadı. Tam durum yenilemesi sonrasında 5 sınav, 1 not ve 1 örnek göründü.

Beklenen: işlemin başarı olayı ilgili liste ve özetleri tutarlı güncellemeli; bunun için açık formu sıfırlamak gerekmemeli.

Kod izi: medical.js markTabs, yerel load/render çağrıları ve study.js olay işleyicileri. Kanıt: canlı DOM/sayı karşılaştırması; yenilenmiş 27-final-state.txt.

### A16 — P2 — Sınav başlığı istenen sayıyı gerçek sayı gibi sunuyor

Mevcut sınav başlığı “Karbonhidrat metabolizması · 10 soru”, içerik 8 soru. İstenen adet ile kabul edilip sınava alınan adet farklı olduğu halde başlık farkı açıklamıyor.

Beklenen: gerçek soru sayısı tutarlı; örneğin “8 soru hazır, 10 istendi” ve mevcut eleme nedenleri. Eski kayıtlar gösterimde de doğru temsil edilmeli.

Kod izi: generation.py ExamBuilder.title_for, medical.js renderExamList. Kanıt: tab-exam, 28-reload-persistence.

### A17 — P2 — Gerekçe değerlendirilirken yanlış kesin sonuç gösteriliyor

Doğru cevap, “Eminim” seçimi ve dolu gerekçe gönderildiğinde ilk ekran “Doğru cevap, gerekçe yok ya da tahmin” dedi. Değerlendirme bitip tekrar bakıldığında gerekçe desteklenmiş görünüyordu.

Beklenen: beklerken “Gerekçe değerlendiriliyor”; yalnız sonuca göre kesin değerlendirme. Gecikme/hata halinde yeniden deneme ve saklı kullanıcı girdisi.

Kanıt: 04-understanding-submitted ve 08-understanding-revisit.

### A18 — P2 — Boş sorular sonuçta zayıf kavram diye gösteriliyor

Üç soruluk sınavda 1 doğru, 0 yanlış, 2 boş varken boşlara ait kavramlar 0/1 ile zayıf kavram listesine girdi. Sonuç, hiç cevaplanmayanı yanlış yapılmış gibi yorumluyor. Boş cevapların kalıcı mastery kaydına yazıldığı bu bulguda iddia edilmiyor.

Beklenen: boş, yanlış ve değerlendirme dışı ayrı; zayıflık değerlendirmesinde cevaplanmış yeterli kanıt şartı. Sınav puanında boşların etkisi ürün kuralına göre ayrıca açıkça gösterilebilir.

Kod izi: questions.py analyse_attempt, concept_stats ve weak_concepts üretimi. Kanıt: 07-exam-results.

## Açık test ve sınırlar

- **T19, sonuçlanmayan modelden sınav üretimi:** Histoloji/Epitel bağlamında ilgili belgenin 34. sayfasından 1 soruluk yeni sınav istendi. “Sınav hazırlanıyor” bildirimi geldi; son gözlem ve yeniden yüklemede yeni Histoloji sınavı veya açık başarısızlık sonucu görünmedi. Önceki sınav ekranda kaldı. Ağ/model yanıtı ve işin terminal durumu doğrulanamadığından “üretim motoru kesin bozuk” sonucu çıkarılmıyor. İş kimliği, görünür bekleme, hata/zaman aşımı, yeniden bağlanma ve tekrar gönderme davranışı ayrıca incelenmeli.
- Belge/klasör içe aktarmanın bütün formatları ve büyük dosya sınırları yeniden denenmedi. 243 belgenin veya 716 sorunun tamamı tek tek okunmadı; tıbbi doğruluk uzman incelemesi yapılmadı.
- Mikrofon, sesli cevap tanıma, sesin işitsel kalitesi, çevrimdışı/sağlayıcı kesintisi, fiziksel tam ekran Esc, farklı çözünürlük ve %125/%150 ölçek regresyonları tamamlanmadı.
- Soru şekillerinin banka/hoca profilinde sunumu, pinlerin tıbbi konumu, veri taşıma ve silme gibi akışlar için ilave test gerekir. Bunlar doğrulanmış yeni hata sayısına eklenmedi.
- Yeniden yükleme sonrası sınav listesi ve %33 sonucu korundu. Uygulamayı bütünüyle kapatıp açma kalıcılığı bu son kontrolde ayrıca doğrulanmadı.

## Düzeltme sırası

1. A01, A03, A04: puan ve öğrenme doğruluğu.
2. A02 ve doğrulandıktan sonra A10: ana akış engelleri.
3. A05–A09, A12, A17–A18: kaynak, kapsam ve değerlendirme sözleşmesi.
4. A06–A07, A11, A13–A16: erişim ve arayüz tutarlılığı.
5. T19 ve açık testler: görünür iş yaşam döngüsü ve kalan kapsam.

Düzeltme için kopyalanabilir görev metni: [FABLE_5_1_PROMPT.md](FABLE_5_1_PROMPT.md).

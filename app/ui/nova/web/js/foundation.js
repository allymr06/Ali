/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — foundation
   Helpers, Turkish vocabulary, shared state, motion primitives, toasts
   and the in-app confirmation dialog. Loaded first; every other module
   builds on what is declared here.

   Honesty contract: everything shown comes from the Python core through
   window.pywebview.api. There is no silent demo. When the page is opened
   directly in a browser with ?demo=1 (and only then) a clearly labelled
   demo bridge serves sample data so the visuals can be inspected.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

/* ── tiny helpers ─────────────────────────────────────────────────── */

const $  = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (err) { return null; }
  return value;
}

function describeError(err) {
  if (err && typeof err === "object" && err.message) return String(err.message);
  return String(err ?? "bilinmeyen hata");
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clamp(value, low, high) { return Math.max(low, Math.min(high, value)); }
const lower = (v) => String(v ?? "").trim().toLocaleLowerCase("tr");

/* ── Turkish vocabulary ───────────────────────────────────────────── */

const TR = {
  pending: "BEKLİYOR", queued: "SIRADA", running: "ÇALIŞIYOR", active: "AKTİF",
  paused: "DURAKLATILDI", completed: "TAMAMLANDI", failed: "BAŞARISIZ",
  blocked: "ENGELLENDİ", cancelled: "İPTAL EDİLDİ", timeout: "ZAMAN AŞIMI",
  success: "BAŞARILI", partial: "KISMİ", unknown: "BİLİNMİYOR",
  waiting_for_input: "GİRDİ BEKLİYOR", waiting_for_approval: "ONAY BEKLİYOR",
  fresh: "GÜNCEL", current: "GÜNCEL", stale: "ESKİ", expired: "SÜRESİ DOLMUŞ",
  read_only: "SALT OKUNUR", low: "DÜŞÜK", medium: "ORTA", high: "YÜKSEK", critical: "KRİTİK",
  healthy: "SAĞLIKLI", degraded: "ZAYIFLAMIŞ", unhealthy: "SORUNLU",
  debug: "AYRINTI", info: "BİLGİ", warning: "UYARI", error: "HATA",
  closed: "KAPALI", open: "AÇIK", half_open: "YARI AÇIK",
  allow: "İZİN", confirm: "ONAY GEREKTİ", deny: "RET",
  user: "KULLANICI", inference: "ÇIKARIM", system: "SİSTEM", imported: "İÇE AKTARILDI",
  conversation: "KONUŞMA", observation: "GÖZLEM", configuration: "YAPILANDIRMA",
  fact: "BİLGİ", preference: "TERCİH", goal: "HEDEF", project: "PROJE", context: "BAĞLAM", instruction: "TALİMAT",
  archived: "ARŞİV",
  overwrite: "ÜZERİNE YAZILDI", delete: "SİLİNDİ", move: "TAŞINDI", plan: "PLAN", undo: "GERİ ALMA",
  tool_verified: "ARAÇ DOĞRULADI", research_supported: "KAYNAK DESTEKLİ", unverified: "DOĞRULANMADI",
  minimal: "ASGARİ", auto: "OTOMATİK", demo: "DEMO",
};
const tr = (v) => TR[lower(v).replace(/-/g, "_")] ?? String(v ?? "").toLocaleUpperCase("tr");

const STATUS_TR = {
  "LOCAL CORE READY": "HAZIR",
  "PROCESSING": "İŞLENİYOR",
  "RESPONDING": "YANITLIYOR",
  "TESTING CONNECTION": "BAĞLANTI SINANIYOR",
  "LISTENING": "DİNLİYOR",
  "CAPTURING": "GÖRÜNTÜ ALINIYOR",
  "RESEARCHING": "ARAŞTIRIYOR",
  "PAUSED": "DURAKLATILDI",
  "RECOVERING": "TOPARLANIYOR",
};
const READY = "LOCAL CORE READY";
const PAUSED_NOTICE = "JARVIS duraklatıldı; önce Devam'ı seç.";

/* Tool names → what the user should read while it runs and once it is
   done. Unknown tools fall back to a humanised form of their name. */
const TOOL_LABELS = {
  launch_windows_application: ["Uygulama açılıyor", "Uygulama açıldı"],
  list_windows_applications: ["Uygulamalar listeleniyor", "Uygulamalar listelendi"],
  list_windows_processes: ["Süreçler okunuyor", "Süreçler okundu"],
  list_allowed_windows: ["Pencereler taranıyor", "Pencereler tarandı"],
  get_windows_system_info: ["Sistem bilgisi okunuyor", "Sistem bilgisi okundu"],
  read_windows_clipboard: ["Pano okunuyor", "Pano okundu"],
  write_windows_clipboard: ["Panoya yazılıyor", "Panoya yazıldı"],
  clear_windows_clipboard: ["Pano temizleniyor", "Pano temizlendi"],
  read_text_file: ["Dosya okunuyor", "Dosya okundu"],
  write_text_file: ["Dosya yazılıyor", "Dosya yazıldı"],
  list_directory: ["Klasör listeleniyor", "Klasör listelendi"],
  list_allowed_file_roots: ["İzinli kökler okunuyor", "İzinli kökler okundu"],
  create_directory: ["Klasör oluşturuluyor", "Klasör oluşturuldu"],
  copy_file: ["Dosya kopyalanıyor", "Dosya kopyalandı"],
  move_file: ["Dosya taşınıyor", "Dosya taşındı"],
  delete_path: ["Dosya siliniyor", "Dosya silindi"],
  search_files: ["Dosyalar aranıyor", "Dosyalar arandı"],
  list_filesystem_snapshots: ["Anlık görüntüler okunuyor", "Anlık görüntüler okundu"],
  undo_filesystem_change: ["Dosya geri yükleniyor", "Dosya geri yüklendi"],
  plan_filesystem_changes: ["Dosya planı hazırlanıyor", "Dosya planı hazırlandı"],
  apply_filesystem_plan: ["Dosya planı uygulanıyor", "Dosya planı uygulandı"],
  research_web: ["Web araştırılıyor", "Web araştırıldı"],
  list_memories: ["Hafıza okunuyor", "Hafıza okundu"],
  search_memories: ["Hafıza taranıyor", "Hafıza tarandı"],
  forget_memory: ["Anı unutuluyor", "Anı unutuldu"],
  delete_memory: ["Anı siliniyor", "Anı silindi"],
  list_tasks: ["Görevler okunuyor", "Görevler okundu"],
  get_task: ["Görev okunuyor", "Görev okundu"],
  pause_task: ["Görev duraklatılıyor", "Görev duraklatıldı"],
  resume_task: ["Görev sürdürülüyor", "Görev sürdürüldü"],
  cancel_task: ["Görev iptal ediliyor", "Görev iptal edildi"],
  diagnostics_health: ["Sağlık denetleniyor", "Sağlık denetlendi"],
  diagnostics_events: ["Olay defteri okunuyor", "Olay defteri okundu"],
  diagnostics_metrics: ["Ölçümler okunuyor", "Ölçümler okundu"],
  create_reminder: ["Hatırlatıcı kuruluyor", "Hatırlatıcı kuruldu"],
  list_reminders: ["Hatırlatıcılar okunuyor", "Hatırlatıcılar okundu"],
  cancel_reminder: ["Hatırlatıcı iptal ediliyor", "Hatırlatıcı iptal edildi"],
  system_volume: ["Ses düzeyi ayarlanıyor", "Ses düzeyi ayarlandı"],
  open_website: ["Web sitesi açılıyor", "Web sitesi açıldı"],
  open_web_search: ["Web araması açılıyor", "Web araması açıldı"],
  watch_screen_start: ["Ekran izleme başlatılıyor", "Ekran izleme başlatıldı"],
  watch_screen_stop: ["Ekran izleme durduruluyor", "Ekran izleme durduruldu"],
  watch_screen_status: ["Ekran izleme durumu okunuyor", "Ekran izleme durumu okundu"],
  spotify_authorize: ["Spotify yetkilendiriliyor", "Spotify yetkilendirildi"],
  spotify_now_playing: ["Spotify'da çalan okunuyor", "Spotify'da çalan okundu"],
  spotify_play_pause: ["Spotify oynat/duraklat", "Spotify oynat/duraklat"],
  spotify_play_track: ["Spotify parça çalınıyor", "Spotify parça çalındı"],
  spotify_next_track: ["Spotify sonraki parça", "Spotify sonraki parça"],
  spotify_previous_track: ["Spotify önceki parça", "Spotify önceki parça"],
  spotify_open_search: ["Spotify araması açılıyor", "Spotify araması açıldı"],
  spotify_create_playlist: ["Spotify listesi oluşturuluyor", "Spotify listesi oluşturuldu"],
  spotify_listening_stats: ["Spotify istatistikleri okunuyor", "Spotify istatistikleri okundu"],
  whatsapp_send_message: ["WhatsApp mesajı gönderiliyor", "WhatsApp mesajı gönderildi"],
  whatsapp_open_chat: ["WhatsApp sohbeti açılıyor", "WhatsApp sohbeti açıldı"],
  whatsapp_read_chats: ["WhatsApp sohbetleri okunuyor", "WhatsApp sohbetleri okundu"],
  whatsapp_read_conversation: ["WhatsApp konuşması okunuyor", "WhatsApp konuşması okundu"],
  whatsapp_list_contacts: ["WhatsApp kişileri okunuyor", "WhatsApp kişileri okundu"],
  whatsapp_add_contact: ["WhatsApp kişisi ekleniyor", "WhatsApp kişisi eklendi"],
  whatsapp_delegate_chat: ["WhatsApp sohbeti devrediliyor", "WhatsApp sohbeti devredildi"],
  whatsapp_delegation_status: ["WhatsApp devri sorgulanıyor", "WhatsApp devri sorgulandı"],
  whatsapp_stop_delegation: ["WhatsApp devri durduruluyor", "WhatsApp devri durduruldu"],
};

function humanizeTool(name) {
  const raw = String(name ?? "").trim();
  if (raw.startsWith("plugin_")) {
    const rest = raw.slice(7).split("_");
    return "Eklenti · " + rest.slice(1).join(" ");
  }
  const words = raw.replace(/[._-]+/g, " ").trim();
  return words ? words.charAt(0).toLocaleUpperCase("tr") + words.slice(1) : "Araç";
}

function toolLabel(name, done) {
  const entry = TOOL_LABELS[String(name ?? "")];
  if (entry) return entry[done ? 1 : 0];
  return humanizeTool(name);
}

const SOURCE_TR = {
  "core:memory": "Hafıza", "core:diagnostics": "Tanılama", "core:tasks": "Görevler",
  "core:research": "Araştırma", "platform:windows": "Windows",
  "platform:windows:clipboard": "Windows · Pano",
  "platform:windows:window-control": "Windows · Pencereler",
  "integration:spotify": "Spotify", "integration:whatsapp": "WhatsApp",
  "integration:system": "Sistem denetimi", "integration:vision": "Ekran izleme",
  "integration:reminders": "Hatırlatıcılar", runtime: "Çalışma zamanı",
};
function sourceLabel(source) {
  const key = String(source ?? "");
  if (SOURCE_TR[key]) return SOURCE_TR[key];
  if (key.startsWith("plugin:")) return "Eklenti · " + key.slice(7);
  return key || "Çekirdek";
}

/* The permission engine's reasons are a small fixed set; the exact
   English sentence is kept as the fallback so nothing is ever hidden. */
const RISK_WORDS = { "read only": "salt okunur", low: "düşük", medium: "orta", high: "yüksek", critical: "kritik" };
const REASON_TR = [
  [/^Tool explicitly requires user confirmation\.$/, () => "Bu araç her kullanımda açık onayını ister."],
  [/^Operation denied by policy\.$/, () => "İşlem ilke gereği reddedildi."],
  [/^Permission rule decision\.$/, () => "İzin kuralı kararı."],
  [/^Tool is denied by the execution scope\.$/, () => "Araç bu yürütme kapsamında yasak."],
  [/^Tool is outside the allowed execution scope\.$/, () => "Araç izin verilen yürütme kapsamının dışında."],
  [/^Effective risk exceeds the execution scope\.$/, () => "Etkin risk yürütme kapsamını aşıyor."],
  [/^Parameter rule '(.+)' requires confirmation\.$/, (m) => `“${m[1]}” parametre kuralı onay gerektiriyor.`],
  [/^Parameter rule '(.+)' denied the operation\.$/, (m) => `“${m[1]}” parametre kuralı işlemi reddetti.`],
  [/^Parameter rule '(.+)' failed closed\.$/, (m) => `“${m[1]}” parametre kuralı güvenli tarafta kaldı.`],
  [/^(Read Only|Low|Medium|High|Critical) operation requires confirmation\.?$/i,
    (m) => `${(RISK_WORDS[m[1].toLowerCase()] || m[1]).replace(/^./, (c) => c.toLocaleUpperCase("tr"))} riskli işlem onay gerektiriyor.`],
  [/^(Read Only|Low|Medium|High|Critical) operations are denied by policy\.?$/i,
    (m) => `${(RISK_WORDS[m[1].toLowerCase()] || m[1]).replace(/^./, (c) => c.toLocaleUpperCase("tr"))} riskli işlemler ilke gereği reddedilir.`],
];
function reasonLabel(reason) {
  const text = String(reason ?? "").trim();
  for (const [pattern, render] of REASON_TR) {
    const match = text.match(pattern);
    if (match) return render(match);
  }
  return text;
}

/* What a risky tool does, in the user's words. Unknown tools show the
   description the core registered. */
const TOOL_EFFECTS = {
  launch_windows_application: "Kayıtlı bir Windows uygulaması başlatılır ve başlatma doğrulanır.",
  write_text_file: "İzinli kök altında bir metin dosyası yazılır ya da üzerine yazılır.",
  delete_path: "İzinli kök altında bir dosya (önce anlık görüntüsü alınarak) ya da boş bir klasör silinir; dosya geri yüklenebilir.",
  undo_filesystem_change: "Bir anlık görüntü özgün yoluna geri yazılır; oradaki dosyanın şu anki hâli de saklanır.",
  apply_filesystem_plan: "Onaylanan plan sırayla uygulanır; her adım anlık görüntü alır ve doğrulanır, ilk hatada durur.",
  move_file: "Bir dosya izinli kökler içinde taşınır.",
  copy_file: "Bir dosya izinli kökler içinde kopyalanır.",
  create_directory: "İzinli kök altında yeni bir klasör oluşturulur.",
  read_text_file: "İzinli kök altındaki bir dosya okunur ve modele iletilir.",
  list_directory: "İzinli kök altındaki bir klasörün içeriği listelenir.",
  read_windows_clipboard: "Pano içeriği okunur ve modele iletilir.",
  write_windows_clipboard: "Panonun içeriği değiştirilir.",
  clear_windows_clipboard: "Pano temizlenir.",
  system_volume: "Sistem ses düzeyi değiştirilir.",
  open_website: "Varsayılan tarayıcıda bir web sayfası açılır.",
  open_web_search: "Varsayılan tarayıcıda bir web araması açılır.",
  research_web: "Web'de arama yapılır ve sayfalar güvenli biçimde indirilir.",
  watch_screen_start: "Ekran düzenli aralıklarla yakalanıp incelenir; hassas bölgeler maskelenir.",
  watch_screen_stop: "Ekran izleme durdurulur.",
  whatsapp_send_message: "WhatsApp'ta seçili kişiye mesaj gönderilir.",
  whatsapp_open_chat: "WhatsApp'ta bir sohbet açılır.",
  whatsapp_delegate_chat: "Bir WhatsApp sohbeti JARVIS'e devredilir; yanıtları JARVIS yazar.",
  whatsapp_stop_delegation: "WhatsApp devri durdurulur.",
  whatsapp_add_contact: "WhatsApp kişi listesine bir kişi eklenir.",
  spotify_play_track: "Spotify'da bir parça çalınır.",
  spotify_play_pause: "Spotify oynatması başlatılır ya da duraklatılır.",
  spotify_next_track: "Spotify sonraki parçaya geçer.",
  spotify_previous_track: "Spotify önceki parçaya döner.",
  spotify_create_playlist: "Spotify hesabında yeni bir çalma listesi oluşturulur.",
  spotify_authorize: "Spotify hesabı için yetkilendirme akışı başlatılır.",
  create_reminder: "Bir hatırlatıcı kaydedilir ve zamanı gelince bildirilir.",
  cancel_reminder: "Bir hatırlatıcı iptal edilir.",
  forget_memory: "Bir anı devre dışı bırakılır; kayıt silinmez.",
  delete_memory: "Bir anı kalıcı olarak silinir.",
  pause_task: "Çalışan bir görev duraklatılır.",
  resume_task: "Duraklatılmış bir görev sürdürülür.",
  cancel_task: "Bir görev iptal edilir.",
};
function toolEffect(name, fallback) {
  return TOOL_EFFECTS[String(name ?? "")] || String(fallback ?? "");
}

const VOICE_PHASE_TR = {
  idle: "SESSİZ", listening: "DİNLİYOR", transcribing: "ANLIYOR",
  processing: "DÜŞÜNÜYOR", synthesizing: "YANIT HAZIRLANIYOR", speaking: "KONUŞUYOR",
  completed: "TAMAMLANDI", ignored: "SES ALGILANMADI", interrupted: "KESİLDİ", failed: "BAŞARISIZ",
};

const SETTING_LABELS = {
  voice_enabled: ["Sesli iletişim", "JARVIS_VOICE_ENABLED"],
  voice_wake_word: ["Uyandırma sözcüğü", "JARVIS_VOICE_WAKE_WORD"],
  voice_require_wake_word: ["Uyandırma sözcüğü zorunlu", "JARVIS_VOICE_REQUIRE_WAKE_WORD"],
  voice_language: ["Konuşma dili", "JARVIS_VOICE_LANGUAGE"],
  voice_gemini_tts_voice: ["Ses karakteri", "JARVIS_VOICE_GEMINI_TTS_VOICE"],
  voice_gemini_stt_model: ["Konuşma tanıma modeli", "JARVIS_VOICE_GEMINI_STT_MODEL"],
  voice_gemini_tts_model: ["Ses sentezi modeli", "JARVIS_VOICE_GEMINI_TTS_MODEL"],
  vision_enabled: ["Görüş", "JARVIS_VISION_ENABLED"],
  vision_detail: ["Görüş ayrıntısı", "JARVIS_VISION_DETAIL"],
  vision_redact_taskbar: ["Görev çubuğu maskelenir", "JARVIS_VISION_REDACT_TASKBAR"],
  research_enabled: ["Web araştırması", "JARVIS_RESEARCH_ENABLED"],
  windows_integrations_enabled: ["Windows entegrasyonları", "JARVIS_WINDOWS_INTEGRATIONS_ENABLED"],
  memory_auto_capture_enabled: ["Konuşmalardan otomatik anı", "JARVIS_MEMORY_AUTO_CAPTURE_ENABLED"],
  memory_extraction_model: ["Anı çıkarım modeli", "JARVIS_MEMORY_EXTRACTION_MODEL"],
  gemini_action_model: ["Eylem modeli", "JARVIS_GEMINI_ACTION_MODEL"],
  gemini_reasoning_effort: ["Muhakeme düzeyi", "JARVIS_GEMINI_REASONING_EFFORT"],
  plugins_enabled: ["Eklenti çalışma zamanı", "JARVIS_PLUGINS_ENABLED"],
  tray_enabled: ["Sistem tepsisi", "JARVIS_TRAY_ENABLED"],
  tray_close_to_tray: ["Kapatınca tepsiye küçült", "JARVIS_TRAY_CLOSE_TO_TRAY"],
  single_instance_enabled: ["Tek örnek", "JARVIS_SINGLE_INSTANCE"],
  approval_ttl_seconds: ["Onay süresi (sn)", "JARVIS_APPROVAL_TTL_SECONDS"],
};
const SETTING_GROUPS = {
  voice: ["voice_enabled", "voice_wake_word", "voice_require_wake_word", "voice_language",
          "voice_gemini_tts_voice", "voice_gemini_stt_model", "voice_gemini_tts_model"],
  vision: ["vision_enabled", "vision_detail", "vision_redact_taskbar", "research_enabled"],
  memory: ["memory_auto_capture_enabled", "memory_extraction_model"],
  models: ["gemini_action_model", "gemini_reasoning_effort"],
  system: ["windows_integrations_enabled", "plugins_enabled", "tray_enabled",
           "tray_close_to_tray", "single_instance_enabled", "approval_ttl_seconds"],
};

/* ── formatting ───────────────────────────────────────────────────── */

function fmtClock(date) {
  return date.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });
}
function fmtTime(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtRelative(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 45) return "az önce";
  if (diff < 3600) return `${Math.round(diff / 60)} dk önce`;
  if (diff < 86400) return `${Math.round(diff / 3600)} sa önce`;
  if (diff < 86400 * 7) return `${Math.round(diff / 86400)} gün önce`;
  return date.toLocaleDateString("tr-TR", { day: "2-digit", month: "short" });
}
function fmtDuration(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} sn`;
  const minutes = Math.floor(ms / 60000);
  return `${minutes} dk ${Math.round((ms % 60000) / 1000)} sn`;
}
function fmtBytes(bytes) {
  if (!Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(0)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
function fmtUptime(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60);
  if (h) return `${h} sa ${m} dk`;
  if (m) return `${m} dk`;
  return `${Math.round(seconds)} sn`;
}
function greetingForHour(hour) {
  if (hour < 6) return "İyi geceler";
  if (hour < 12) return "Günaydın";
  if (hour < 18) return "İyi günler";
  return "İyi akşamlar";
}

/* Subsequence match with a light score: contiguous runs and word starts
   rank higher. Returns null when the query does not match. */
function fuzzyScore(query, text) {
  const q = lower(query), t = lower(text);
  if (!q) return 0;
  if (t.includes(q)) return 100 - t.indexOf(q);
  let ti = 0, score = 0, streak = 0;
  for (const ch of q) {
    const index = t.indexOf(ch, ti);
    if (index < 0) return null;
    streak = index === ti ? streak + 1 : 0;
    score += 2 + streak * 2 + (index === 0 || t[index - 1] === " " ? 4 : 0);
    ti = index + 1;
  }
  return score;
}

/* ── shared state ─────────────────────────────────────────────────── */

const State = {
  screen: "home",
  snapshot: null,
  settings: null,
  runtime: null,
  conversations: [],
  fileRoots: { available: false, roots: [] },
  snapshots: [],
  messages: [],
  voiceMessages: [],
  busy: false,
  voiceActive: false,
  voicePhase: null,
  voiceLevel: 0,
  core: "offline",          // see Presence in presence.js
  pendingEl: null,          // streaming assistant message
  approvals: [],            // session approval log
  diagnosticEvents: [],     // live ledger tail
  requestDurations: [],     // real per-request seconds from request.completed
  lastStatus: null,         // last system_status() answer
  /* Motion is the interface's language, so it defaults ON regardless of
     the OS-wide animation toggle; the in-app switch persists an explicit
     opt-out. */
  reducedMotion: store("nova.motion") === "off",
  ambient: store("nova.ambient") !== "off",
  demo: false,
  booted: false,
  paused: false,            // tray "Duraklat": new work is refused
  compact: false,
  contextOpen: store("nova.context") === "open",
  railCollapsed: store("nova.rail") === "collapsed",
  pointer: { x: 0.5, y: 0.5 },
};

/* ── motion primitives (one vocabulary, used everywhere) ──────────── */

const Motion = {
  fast: 120, normal: 200, panel: 300, cinematic: 620,
  enter: "cubic-bezier(0.16, 1, 0.3, 1)",
  standard: "cubic-bezier(0.2, 0, 0, 1)",
  exit: "cubic-bezier(0.4, 0, 1, 1)",
  allowed() { return !State.reducedMotion; },
  /* fade + small rise */
  rise(node, { y = 10, duration = Motion.panel, delay = 0, scale = 1 } = {}) {
    if (!node || !Motion.allowed()) return null;
    return node.animate(
      [{ opacity: 0, transform: `translateY(${y}px) scale(${scale})` },
       { opacity: 1, transform: "translateY(0) scale(1)" }],
      { duration, delay, easing: Motion.enter, fill: "backwards" });
  },
  fade(node, { duration = Motion.normal, from = 0, to = 1 } = {}) {
    if (!node || !Motion.allowed()) return null;
    return node.animate([{ opacity: from }, { opacity: to }], { duration, easing: Motion.standard });
  },
  /* fade + drop, resolves when done (or immediately without motion) */
  leave(node, { duration = Motion.normal, y = 6 } = {}) {
    return new Promise((resolve) => {
      if (!node || !Motion.allowed()) { resolve(); return; }
      const anim = node.animate(
        [{ opacity: 1, transform: "translateY(0)" }, { opacity: 0, transform: `translateY(${y}px)` }],
        { duration, easing: Motion.exit, fill: "forwards" });
      anim.onfinish = () => resolve();
      setTimeout(resolve, duration + 60);
    });
  },
  stagger(nodes, { step = 40, max = 12, y = 12 } = {}) {
    if (!Motion.allowed()) return;
    nodes.forEach((node, index) => Motion.rise(node, { y, delay: Math.min(index, max) * step, duration: 360 }));
  },
};

/* ── toasts ───────────────────────────────────────────────────────── */

function toast(text, kind) {
  const host = $("#toasts");
  if (!host) return;
  const node = el("div", `toast ${kind === true || kind === "err" ? "err" : kind === "ok" ? "ok" : ""}`);
  node.textContent = text;
  host.appendChild(node);
  Motion.rise(node, { y: 8, duration: Motion.panel });
  setTimeout(() => Motion.leave(node).then(() => node.remove()), 5200);
}

/* ── in-app confirmation (UI safety net, unrelated to tool approvals) ── */

let confirmOpen = false;

function confirmDialog({ title, body, confirmLabel = "ONAYLA",
                         cancelLabel = "VAZGEÇ", danger = false }) {
  return new Promise((resolve) => {
    const veil = $("#confirm");
    const ok = $("#confirm-ok"), cancel = $("#confirm-cancel");
    const previous = document.activeElement;
    $("#confirm-title").textContent = title;
    $("#confirm-text").textContent = body;
    ok.textContent = confirmLabel;
    cancel.textContent = cancelLabel;
    ok.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
    const finish = (value) => {
      ok.onclick = null; cancel.onclick = null;
      window.removeEventListener("keydown", onKey, true);
      veil.hidden = true;
      confirmOpen = false;
      if (previous && typeof previous.focus === "function" && document.contains(previous)) previous.focus();
      resolve(value);
    };
    const onKey = (event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finish(false); }
    };
    ok.onclick = () => finish(true);
    cancel.onclick = () => finish(false);
    window.addEventListener("keydown", onKey, true);
    confirmOpen = true;
    veil.hidden = false;
    Motion.rise(veil.querySelector(".modal"), { y: 14, scale: 0.97, duration: Motion.panel });
    cancel.focus();   // the safe choice is the default focus
  });
}

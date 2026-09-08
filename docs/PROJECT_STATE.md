# JARVIS Project State

Last verified: 8 September 2026

## Current status

- Completed implementation milestone: Phase 17 — Windows packaging and installer
- Completed validation milestone: Phase 18 — final audit and delivery evidence
- Completed maintenance milestone: single-provider (Gemini) consolidation
- Completed stabilization milestone: Nova desktop shell (pywebview/WebView2),
  5 September 2026
- Completed feature milestone: manifest-based plugin runtime v1 (in-process,
  disabled by default), 5 September 2026
- Completed feature milestone: Windows system tray with single-instance
  behaviour, 5 September 2026
- Completed interface milestone: Nova cinematic interface redesign (design
  system, presence-driven core, live execution timeline, command palette,
  compact window, voice stage), 5 September 2026
- Completed feature milestone: safe-filesystem extensions (verified
  snapshots with recoverable delete and undo, dry-run plans applied by
  digest, bounded name search, critical-directory block, Nova file-access
  settings), 5 September 2026
- Completed feature milestone: Nova notification centre and attention
  routing (reminders delivered in the Nova shell, unattended replies,
  approvals and results collected, ledger warnings and screen observations,
  native notification when the window is hidden), 5 September 2026
- Completed performance milestone: Gemini 3 tool turns (signed replays,
  streamed answers on tool turns, lighter finalization, no default
  escalation, quota cooldown, connection warm-up, per-call latency
  diagnostics), 5 September 2026
- Completed feature milestone: scheduled routines (a named prompt JARVIS
  runs on its own daily at a clock time or every N minutes, through the
  same core and approvals, outcome delivered to the notification centre),
  5 September 2026
- Completed performance milestone: desktop start-up (speech and model
  clients built on first use or by the boot warm-up instead of before the
  window opens; in-process start to boot 4.3 s to 0.8 s), 5 September 2026
- Completed performance milestone: voice time to first audio (streamed
  Gemini speech played as it arrives, first sentence synthesized while the
  reply is still streaming), 5 September 2026
- Completed feature milestone: Medical Academy — a first-year medical-school
  study layer (curriculum, terminology, concept graph, PDF study engine with
  page-anchored citations, tutor, question and exam engine, evidence-based
  professor-style profiling, interpretable mastery with spaced review, and the
  Anatomy Lab), 6 September 2026
- Completed feature milestone: Anatomy Lab in 3D (BodyParts3D scene of the
  right upper limb, free navigation, detailed pins, bell-ringer exam),
  7 September 2026
- Completed feature milestone: first-year gross-anatomy foundation across
  every region — the skull, cranial nerves, craniovertebral and jaw joints and
  carotid system; the vertebral column, thoracic cage and abdominal wall; both
  limbs with their vessels — 127 curated structures, five importer scenes, and
  the reference tables turned into recall questions, 7 September 2026
- Completed fix: one consistent cloud voice, with the free-tier speech quota
  named to the user when it runs out, 7 September 2026
- Completed feature milestone: the semester's lecture folder as one lecture
  set (presentations through PowerPoint, lecturers read from the title slides
  and their review questions filed under them, a professor-style paper drawn
  from that lecturer's own lectures) and sesli anlatım — a lecture read aloud
  with questions answered in between, 8 September 2026
- Completed reliability repair: a bell-ringer station is claimed before
  its save and counted once, an exam is finalized once per attempt with its
  adaptive verdict stored, and a reminder whose delivery failed is leased,
  retried and kept instead of lost, 8 September 2026
- Completed feature milestone: Medical Academy expansion (confidence and
  reasoning with every answer, misconception findings with diagnosis and
  repair, prerequisites with provenance and prerequisite diagnosis, the
  exam-date plan with curriculum coverage and "Bugün", histology practicals
  from the student's own page figures, source-support review with the
  student's flag and invalidation), 8 September 2026
- Next action: plugin process isolation; code signing and a user-attended
  voice qualification remain release blockers (`docs/FINAL_AUDIT.md`)
- State: development release; production acceptance is not yet achieved
- Platform target: Windows 11, Python 3.12
- Automated verification: 2425 tests passing, 4 skipped (`scripts/verify.py`, Python 3.12.8 on Windows 11, 8 September 2026)
- Production readiness: not yet claimed

## The study workflow in the live window (9 September 2026)

The five features were driven in the running Nova window against Gemini, on a
copy of the study store (243 documents, 11 948 pages, 706 imported questions),
through the page's own handlers over the WebView2 debugging port. Everything
below was observed, not inferred; six defects came out of it and are fixed.

- **A practice answer was recorded twice.** The page sends its own submission
  token with an answer; finishing the paper sent the attempt's. The event's
  identity is the attempt and the question in both places now, so a finding is
  no longer fed the same answer twice.
- **A confirmed plan was not laid out until "Bugün" was opened**, so the first
  summary said everything fit with nothing planned. Creation plans at once.
- **A manual activity could not be added to a full day.** Only fixed work
  (manual, started, completed) counts against the budget; the automatic
  activities are planned again around the new entry.
- **Every wrong answer in a subject fed one finding.** The imported committee
  questions name no concept, so findings were called "Anatomi konusunda yanlış
  cevap" and the repair retrieved a history-of-medicine page for the subject
  name. Findings are now anchored to a concept read from the question, or to
  the question itself.
- **A distractor named a finding**: a question about the deep peroneal nerve
  was filed under *nervus obturatorius* because that was one of the wrong
  options. The concept is read from the stem, the key and the explanation.
- **The repair explanation called the correct answer the misunderstanding.**
  The prompt now carries the question, the chosen option and the key, and
  forbids restating the key as the error. The live re-run explained the actual
  mistake (a sensory nerve chosen for a motor function).
- Two labels leaked English (`model:unknown`, `açıklama specific`) and the
  prerequisite rows showed raw provenance keys; all three are Turkish now.

What the pass confirmed, end to end: a plan with a confirmed scope laid out
within a 50-minute budget; Başla / Bitti / Atla moving one activity through
started, completed (12 real minutes, which the estimate then learned) and
skipped, with a manual entry surviving a replan inside the budget; a practice
sitting carrying confidence and asking for a reasoning, the model's verdict
coming back as a classification; a repair session quoting the student's own
lower-limb lectures, explaining the mistake and asking a transfer question
about the lateral malleolus (similarity 0.042); a histology specimen cut from
a lecture page (a 100 KB crop rendered from the PDF), hidden in a timed
session, identified with folding and its explanation graded *özgül* with the
features named; the source-support gate quarantining one of two generated
questions and saying so on the paper; a flag and then an invalidation that
kept the attempt, corrected one mastery row and withdrew one finding.

Also repaired here: two tests that failed on load rather than on behaviour —
a source-review test that named an alternative option letter (generation
re-letters options with a random seed, so the letter sometimes became the
key), and the desktop UI pump test, whose one-second wall-clock bound failed
on a busy machine.

## Medical Academy expansion (8 September 2026)

Five connected capabilities, one journey: an exam is planned, "Bugün" says
what to do, an answer carries the student's confidence, a contradictory
reasoning opens a finding, the diagnosis looks at a prerequisite, a repair
session asks a transfer question, the delayed review resolves the finding and
the plan's coverage moves (`tests/test_medical_study.py` runs it end to end
with a scripted model and a restart in the middle).

- **Confidence and reasoning.** `QuestionAttempt` carries `confidence` and
  `reasoning`; every answer path (exam, anatomy, check, diagnosis, repair,
  histology) accepts them with a `submission_id`. `UnderstandingEngine`
  records events, samples reasoning (two per exam, always on an open
  finding), assesses it with one bounded model call, and classifies the
  answer. The mark never changes.
- **Findings and repair.** Weighted evidence opens a hypothesis and supports
  it at two; challenge, dismiss, reopen with notes; a diagnostic question
  whose expected answer never reaches the page; a five-step repair with a
  generated transfer question and a delayed follow-up in the review queue;
  resolution only from a later correct answer on the concept.
- **Prerequisites.** 63 seeded curriculum edges with reasons; suggestions
  from material, the model or the student stay pending until confirmed;
  cycles refused; a diagnosis that asks at most three bank questions,
  locates the foundation and lays out the way back.
- **Planner.** Plans with a confirmed scope, weekday budgets, days off,
  reminders; six coverage states with "studied" kept apart from
  "demonstrated"; a day never over budget; labelled estimates refined from
  actual durations; missed days and overload reported with numbers; replans
  that keep completed and manual work; "Bugün" on the dashboard.
- **Histology.** A rectangle on a lecture page becomes a specimen with a
  fresh 2× crop; name and features need a basis; a model description is
  never the answer; study and timed sessions that hide the answer, match the
  identification with folding, grade the explanation separately, and say
  when repeated images inflated the score.
- **Source support.** Generated questions are reviewed against their passage
  before a paper (twelve per paper at most) and carry a status the bank
  shows; imported keys are never edited; "Soruda hata olabilir" flags with
  four kinds; invalidation keeps the attempt and corrects mastery and
  findings. `JARVIS_MEDICAL_SOURCE_REVIEW` (default on).
- **Storage.** `MedicalStore` schema 2: `records` and `media` tables, a
  recorded migration, `transaction()`; every record kind listed in
  `docs/MEDICAL_ACADEMY.md`.
- **Nova.** Tabs Plan, Anlama, Histoloji; dashboard cards Bugün and Anlama;
  confidence chips, the "Kısaca neden?" box and the flag button in the
  runner; reasoning boxes for wrong answers in the results; support chips,
  "Kaynağı incele", the flag and "Geçersiz say" in the bank; the region
  selector on a library page; study jobs reported by push. Verified in the
  static demo page with stubbed bridge replies, in QuickJS, and then in the
  native window against Gemini (see the entry above).

## Reliability repairs (8 September 2026)

A source review of commit `59b0ba3` (7 September) with small executable
reproductions found three defects; each is repaired with the smallest
coherent change and pinned by regression tests that fail on the unrepaired
code (run in a throwaway worktree of the previous master: 22 of the new
tests failed there and the reminder-lifecycle module did not import).

- **A bell-ringer station could be answered twice.** `answerStation` cleared
  `bell.current` only after awaiting the save, so a second click, a second
  Enter, or the bell landing on a manual answer produced two answer records,
  two `anatomy_answer` calls and two `nextStation` calls — a skipped station
  and a mastery history with an answer it never gave. The station is now
  claimed synchronously before the first await (`bell.current` cleared,
  the timer stopped, `bell.pending` set), so at most one submission is
  accepted per station and the exam advances exactly once, after the
  academy accepted the save. Grading uses the pin fixed when the station
  opened (`bell.landmark`, `bell.specimen`), not whatever the student
  selected meanwhile, and a station whose specimen is still loading cannot
  be answered. Every reply carries the exam's run number and its own
  pending record: a reply for a finished or restarted exam changes nothing.
  A save the academy refused or the bridge failed is shown as such
  ("Cevap kaydedilemedi: …") with "Tekrar dene" and "Kaydetmeden geç"; the
  retry sends the same `submission_id`, and `record_anatomy_answer` answers
  a repeated id from a bounded memory without moving mastery again, so a
  reply lost on the way cannot double-count. The results list marks an
  answer that was never saved.
- **Finishing an exam twice moved the difficulty twice.** `finish_exam`
  guarded the mastery updates with `finished_at` but recomputed and applied
  the adaptive difficulty on every call (3 → 4 → 5 for one perfect paper),
  and it saved the attempt before adding the adaptive verdict, so the
  returned result — reloaded from the store — never carried the
  explanation. Finalization now runs once per attempt under the academy
  lock and inside one store transaction (`MedicalStore.transaction`):
  mastery, the analysis with its adaptive verdict, the session's
  difficulty and the exam's status are committed together or not at all,
  a repeated request for a finished attempt returns the stored result (its
  own previous/suggested/reason, never recomputed from the session's later
  difficulty) and emits no second completion event, and a genuinely new
  attempt at the same exam is finalized on its own. Immediate-feedback
  sittings still record each answer once, as it is given.
- **A reminder whose delivery failed was gone for good.** `claim_due`
  marked reminders delivered before delivering them, and the watch
  swallowed a raising callback, so a reminder due while the centre was
  unavailable was attempted once and vanished. The reminder store now
  leases what it hands out: `claim_due` (one immediate transaction, so
  concurrent pollers never take the same reminder) stamps a claim token and
  counts the attempt; `acknowledge` marks delivery only when the token
  still holds; `release` gives a failed claim back with the error and a
  growing delay (30 s doubling to 15 min); after five failures the reminder
  stays in the active list as "teslim edilemedi" and in
  `list_undeliverable()` rather than being retried forever; a claim nobody
  settled expires with its 120 s lease and is handed out again; cancelling
  reaches a waiting, claimed or retrying reminder alike. `ReminderWatch`
  settles claims when the source offers `acknowledge`/`release` and
  otherwise keeps the older contract, so scheduled routines (which move a
  routine to its next slot as they claim it) are unchanged; the classic
  desktop loop settles claims the same way. The guarantee is stated
  precisely: at-least-once delivery to the callback and exactly-once
  acknowledgment per claim token. Delivery in Nova means the notification
  centre accepted the entry; the native toast is a courtesy whose failure is
  not a failed delivery. The reminder's id is the entry's reference, and
  `_deliver_reminder` asks the centre (`find`) before publishing, so a
  reminder handed out again after a crash or an expired lease is not shown
  twice. Existing databases migrate in place (`ALTER TABLE` for the six
  lifecycle columns, repeatable, nothing deleted; active, cancelled and
  delivered rows keep their meaning).

Regression coverage: `tests/test_nova_web.py` (QuickJS with deferred
requests: one answer per station across click/Enter/bell races, failed and
rejected saves with retry and skip, finishing and restarting while a save is
pending, grading against the station's own pin, an unloaded station),
`tests/test_medical_exams.py` (3 → 4 → 4, an unsuccessful paper lowered
once, the verdict in the returned and the reloaded record, a verdict kept
after the session changes, a new attempt on its own, immediate feedback
counted once, four concurrent finalizations with one completion event,
short and unanswered papers, disabled adaptive difficulty, a failure
mid-finalization rolled back, a repeated bell-ringer submission recorded
once), `tests/test_reminder_delivery.py` (a controllable clock and temporary
databases: success, failure then retry, bounded back-off and exhaustion,
restart after claim, restart after the centre accepted the entry,
concurrent pollers and loops, cancellation at every stage, migration of the
old schema, the routine store under the watch), `tests/test_notifications.py`
(the watch acknowledging and releasing, a source without leases, settling
errors, the centre's `find`) and `tests/test_ui_nova.py` (a repeated
hand-out shown once, a broken toast not a failed delivery, a real reminder
service acknowledged through the bridge). Verification: the whole suite
through `scripts/verify.py` on this machine; no live Windows end-to-end
run (real toasts, a real bell-ringer sitting in WebView2) was performed, and
none is claimed.

Recorded, not done — outside this repair: full-body anatomy beyond the
regions listed under "Not yet built" in `docs/MEDICAL_ACADEMY.md`, general
keyboard/mouse automation and general safe PowerShell, plugin process
isolation, and any provider migration remain follow-up work.

## The committee papers (8 September 2026)

The past exams were in a second folder Ali shared: eight PDFs, one per
subject (*Anatomi Tüm Komiteler Çıkmış* … *Tıbbi Biyoloji Tüm Komiteler
Çıkmış*), 131 pages, every one a scan with no text layer. They are an exam
system's export: a `KOMITE N` heading per page and, under every question, the
author (`Soru Sahibi :`), the department and the options with the key marked
as a suffix. Ali asked for one thing from them: the lecturers' styles.

- The parser reads the export (owner, department, committee, key suffix, the
  student's own mark kept apart from the key, two keys naming none) and the
  mining files each question under its own owner, merging `RABET GÖZİL` with
  the `Prof. Dr. Rabet Gözil` already known from the lectures; such a paper
  belongs to nobody as a whole.
- Scanned pages are transcribed by the vision model before anything else
  (`transcribe_document`, first step of `continue_processing`), exactly as
  printed, with the figure pass's pacing and outage rule; the transcription
  becomes the page's text, so the deterministic parser, the search and the
  narration see the paper like any text PDF.
- The download of a Drive folder keeps a file's id in its name when another
  file in the same folder has the same name, so nothing is overwritten again.
- **The vision quota stopped at 37 of 131 pages** (Anatomi, Biyofizik, the
  first page of Biyokimya). Ali asked for the rest to be read now, so the
  remaining 94 pages were rendered and transcribed by hand into the same
  export format the model produces (headings, numbers, owner and department
  lines, options with their suffixes; a figure described in one line in
  parentheses; handwritten notes on the scans ignored), stored as the pages'
  text through the same path (`save_page`, `reindex`, the *taranmış metin*
  tag), and mined with "Hocaları ayır". Nothing was corrected or guessed: a
  question whose owner lines are missing from the print stays ownerless.
- **What the scans taught the parser.** A key suffix wrapped onto the next
  line lost the key (three Biyofizik questions); options printed beside a
  figure above the owner lines lost their key and options (one more); a
  question without owner lines lost its options to the stem; and six
  questions that open with "Aşağıdaki ifadelerden hangisi yanlıştır?" were
  skipped as duplicates of each other. All four are fixed with tests: the
  suffixes are re-read on a continuation line, held-back option lines become
  the options when none follow the owner lines, the hold applies only to a
  block that has owner lines, and a question is known by its stem *and*
  options (`question_fingerprint`).
- **State at the end of the session.** All 131 pages of the eight papers are
  text (37 by the model, 94 by hand from the renders), every paper parses
  to exactly the number of `N. soru:` starts it prints, and 476 questions
  are filed — every one with the key the paper marks — under 21 owners:
  Burcu Baba 59, Müge Öçal Demirtaş 38, Hakkı Yeşilyurt 35, Cumhur Bilgi 34,
  Özgül Kısa 32, Şerife Cankurtaran Sayar 31, Kadirhan Sunguroğlu 28, Dilek
  Yonar 25, Pelin Telkoparan Akıllılar 24, Sami Aydoğan 24, Rabet Gözil 21,
  Pınar Şahin 16, Gülsen Güneş 16, Çağla Zübeyde Köprü 15, Gizem İlter Aktaş
  15, Ülker Çuhacı 12, Çiğdem Özer 12, Burcu Akkurt 10, Saide Muratoğlu 10,
  Ayşe Gülnihal Canseven Kurşun 9, Çiğdem Çiçek 8; nine of them merged with
  the profile already read from their lectures. Two questions have no owner
  lines in the print (Biyofizik K1 no. 15, Biyokimya K5 no. 70) and stay
  ownerless; the Mikrobiyoloji paper starts at question 57 with no committee
  heading on any page, so its questions carry none. The model's transcription
  of one Anatomi option had swallowed a handwritten note after the key
  suffix; the page text was corrected to what is printed. Two answer
  distributions stand out as real signals: Cankurtaran Sayar keys A in 22 of
  31, Gülsen Güneş in 12 of 16.

## What the shared Drive folder held (8 September 2026)

Ali expected the committee past-exam papers in the shared folder. They are not
in it. The folder's 236 files — listed in full and each one imported — are
lecture decks, lab handouts and two published textbooks; no file is an exam
paper, and a search of every imported page for exam markers (answer keys, "A
grubu", numbered questions with lettered options) finds questions only inside
lectures and those textbooks. Two caveats are recorded honestly: the listing
was fetched anonymously, so a file inside the folder that is not shared with
"anyone with the link" would be invisible to it; and two pairs of files shared
a name inside their folder (`Halk Sağlığı.pdf`, `Küresel İklim Krizi ve
Sağlık.pdf`), so the first download overwrote one of each pair — both copies
were fetched again under distinct names and imported, taking the set to 235
documents.

The audit that followed changed two things in the code:

- **A book is not a lecturer.** The 560-page *Halk Sağlığı* is an Ankara
  University distance-learning textbook; its editor and authors had become
  four "lecturers". Front matter is now recognised (ISBN, or a publisher's
  mark with an editorial one), the book's questions are kept under nobody and
  tagged *kitaptan*, and the real lecturer list fell to 24 people.
- **A pictured cover still names its lecturer.** Ten decks (the microbiology
  block) are scans with no text at all, so their title slides named nobody.
  What the vision pass writes about page 1 is now read as well — bounded to
  the first pages, to pages with no text of their own, and to descriptions
  that say they are a cover, so a portrait inside a lecture never becomes the
  lecturer.

## The semester's lectures, their lecturers and sesli anlatım (8 September 2026)

Ali shared the term's Google Drive folder (HUP, KDT, Komite 1–5: 236 files,
1.42 GB) and asked for the lectures to be separated for JARVIS, figure
questions from them, a voiced narration mode with questions in between, the
past questions split by the lecturer named in the heading, an audit of the
result, and a push.

- **Lecture sets.** A folder imports as one unit (`import_folder`,
  `process_lecture_set`, `import_folder_job`): folder names become tags, the
  nearest folder or file name that names a subject sets the subject, the set
  record lives in the store's `meta` table, counts are computed from the
  documents, and a batch member's completion is `quiet` so the set notifies
  once. The library screen gained "Klasör ekle", the set cards with their
  counts and actions, and a document search box.
- **Presentations.** 56 of the files were `.ppt`/`.pptx`. `OfficeConverter`
  exports a deck to PDF through the installed PowerPoint 2013 (COM from a
  PowerShell script, read-only, cached by the deck's SHA-256 under
  `converted/`); `JARVIS_MEDICAL_OFFICE_CONVERSION` turns it off; tests never
  drive PowerPoint (`conftest.py`). A `.pdf` that is really a deck is sniffed.
  Titles: the file's own name unless it says nothing, the PDF Title only when
  it is not a person or a tool default, then the first heading; an empty text
  file is refused; the page limit default rose to 800 (one public-health deck
  had 560 pages).
- **Professors from the material.** The folder holds lectures, not exam
  papers: a scan of every imported page found no compiled question file, only
  review questions inside some decks. So `mine_questions` reads the lecturer
  from the title slide or the file name (`professor_mentions`,
  `professor_from_title`, `same_person`, `fuller_name`), stamps each lecture,
  files its questions under the lecturer (`lecture_derived`, page-anchored,
  figure page when pictured, keys never guessed) and reports the rest. A
  professor-style paper now draws on that lecturer's own lectures.
- **The audit.** The first real run was wrong in two ways and both are fixed
  with tests: a bare abbreviation opening a sentence (`Arş. Geliştirme…`,
  `Öğr. Elemanları…`, `Yrd. Üreme…`) had become a lecturer, and lectures were
  being cut at every name mentioned inside them (history lectures produced
  `Dr. Refik Saydam`). Now only a real rank names a person and only a file
  that looks like a compiled paper is split. The false profiles and their
  questions were removed from the store and the set was mined again.
- **Result on the real set.** 233 documents imported, 233 ready (the 560-page
  deck and a `.pdf` that was a `.pptx` fixed in the second pass); 162 lectures
  attributed to 25 lecturers (the largest: Müge Öçal-Demirtaş 25, Ayşe
  Gülnihal Canseven Kurşun 21, Ayla Kürkçüoğlu 13, Şeyma Aliye Kara 11, Şükrü
  Oğuz Özdamar 11, Rabet Gözil 10); 179 review questions filed, none with a
  stated key, four anchored to a pictured page; 71 lectures name nobody
  (Komite 2 biochemistry, Komite 3 microbiology, several anatomy lab
  handouts) and are reported as such.
- **The figure pass.** Run against the real provider on the anatomy lectures
  first (57 documents, up to twelve figure pages each): the free tier refused
  a burst of calls within two minutes, so the pass now paces itself, waits
  after a refusal, stops a document after two refusals with its pages left
  pending, and `continue_processing` ("Şekilleri incele" on a set or a
  document) resumes the pending figures and missing analyses later. Figure
  questions are generated from the pages the pass described; the other
  subjects' figure pages wait for quota.
- **Sesli anlatım.** `app/medical/narration.py`: script built by the model in
  six-page batches (or the pages read as they stand, and the script says so),
  a chunked player with pause/resume/next/prev/stop, typed and spoken
  questions answered from the segment and its pages and the reading resumed
  at the same chunk, checkpoints every three segments, the local Windows
  voice by default and the cloud voice on request with a fallback that names
  the quota. Narration and the voice session exclude each other. The page
  shows the narration panel above the academy tabs; a document's "Sesli anlat"
  starts it.
- **Left as is.** The Drive folder was downloaded to
  `%LOCALAPPDATA%\JARVIS\medical\imports\drive_dersler` and its documents are
  copied into the academy directory; the download can be deleted once the
  library is trusted. Two files not part of this work were found in the
  working tree (`app/voice/chatterbox_worker.py`, `app/voice/profiles.py`, a
  `.gitignore` line for voice profiles) and left uncommitted; that
  voice-clone experiment was cancelled on 8 September 2026 and removed
  from the working tree, the local branches and this document.

## Lower-limb vessels (7 September 2026)

The lower limb gains its vasculature, mirroring the upper limb (both now carry
four arteries and two veins). An overview card (vasa_membri_inferioris) carries
the arterial line from external iliac through femoral, popliteal and the tibial
arteries with each name change at its landmark, and the femoral-triangle
contents lateral to medial (NAVEL: nerve, artery, vein, empty canal,
lymphatics), with the pulse points and the great saphenous vein's constant
landmark in front of the medial malleolus. Cards were added for the femoral,
popliteal and anterior and posterior tibial arteries and the femoral and great
saphenous veins. The anatomy data is 127 structures.

## Head and neck vessels (7 September 2026)

The carotid system rounds out the head and neck, which already had its bones,
joints, muscle and the twelve cranial nerves. An overview card (vasa_colli)
carries two tables — the eight branches of the external carotid and the
contents of the carotid sheath — with the high-yield distinction that the
internal carotid gives no branch in the neck, the carotid sinus and body at the
bifurcation, and the anterior (carotid) versus posterior (vertebrobasilar)
brain circulation. Cards were added for the common, internal and external
carotid arteries, the vertebral artery and the internal jugular vein, each with
origin, course, branches or tributaries, territory and high-yield. A new
curriculum branch, anatomy.cardiovascular.head_neck_vessels, holds them. The
anatomy data is 120 structures.

## The abdominal wall (7 September 2026)

The anterolateral abdominal wall completes the trunk. An overview card
(paries_abdominis_anterior) carries the two tables every first-year is asked
for — the rectus sheath above and below the arcuate line (below it the
posterior wall is only transversalis fascia) and the walls, rings and contents
of the inguinal canal (the spermatic cord or round ligament, the deep ring
lateral to the inferior epigastric artery) — with the Hesselbach triangle and
the direct-vs-indirect hernia distinction. Cards were added for the three flat
muscles (external and internal oblique, transversus) and rectus abdominis, each
with origin, insertion, innervation, action and high-yield. No new geometry;
the flat sheets stay schematic. The anatomy data is 114 structures, and the
trunk — vertebral column, thoracic cage and abdominal wall — is now complete.

## The thoracic cage (7 September 2026)

The rib cage completes the trunk skeleton the vertebral column started. An
overview card (cavea_thoracis) sorts the ribs into true, false and floating in
one table and lays out a typical rib's parts (head, neck, tubercle, body,
costal groove) in another, with the clinical high-yield (the neurovascular
bundle in the costal groove, the subclavian structures over the first rib, the
sternal angle as the counting landmark). Cards were added for the typical rib,
the atypical first rib (its scalene tubercle and subclavian grooves) and the
floating ribs; the sternum card already shipped and now gains its mesh. The
importer's thoracic_cage scene draws a rib cage — the ribs in grouped colours,
the three-part sternum and the thoracic spine together. The anatomy data is 109
structures.

The table-recall quiz gained a small correctness fix found here: a reference
table cell that is a dash placeholder ("—", meaning none) is no longer offered
as a quiz answer, since an empty answer makes every distractor trivially match.

## Craniovertebral and jaw joints (7 September 2026)

The capstone that ties the skull and the spine together: three joint cards.
The temporomandibular joint (its articular disc splitting it into a gliding
and a hinging compartment, the openers and closers, anterior dislocation), and
the atlanto-occipital and atlanto-axial joints — the 'yes' of nodding and the
'no' of rotation around the dens, with the transverse ligament that keeps the
dens off the cord. Each is a curated joint card with the documented fields
(type, surfaces, capsule, ligaments, movements with plane and axis, muscles)
and is quizzed on its joint type; the cards name the bones they connect. No new
geometry. The anatomy data is 105 structures.

## Vertebral column (7 September 2026)

The spine, the next foundational block of the axial skeleton. An overview card
carries the regional-count table (each region with the one feature that
identifies it) and the curvature table (primary vs secondary), and cards were
added for the atypical atlas and axis and the typical cervical, thoracic and
lumbar vertebra plus the coccyx — the sacrum card already shipped, half-built,
so the column is now whole. The importer's vertebral_column scene draws a
continuous spine by merging each region's run of vertebrae into one coloured
group (T1-T12 as one mesh), so the whole column renders in six colours and its
regions hide one at a time to show the curvatures; the single bones (atlas,
axis, sacrum) carry derived pins, the merged groups do not. The anatomy data is
102 structures. Curriculum leaf anatomy.musculoskeletal.trunk.vertebral_column,
already present. Curated from the standard texts.

## Quiz from the reference tables (7 September 2026)

The new region cards — the cranial-nerve overview, the skull base, the orbit
and sinus tables — carry their knowledge as tables, not landmarks, so the
landmark quiz left them silent. The Anatomy Lab quiz now also draws on a card's
tables: given a row's subject it asks another column's value (a nerve's skull
exit, a foramen's bone, a sinus's meatus) with the other rows' values as
distractors. A verbose cell is skipped row by row rather than dropping its
whole column, candidates are pooled and shuffled across columns, and the
catalogue-wide property test was extended to prove no table distractor is a
second true answer.

## The full skull: viscerocranium (7 September 2026)

The facial skeleton finishes the skull. An overview card carries the orbit-wall
and paranasal-sinus tables (which meatus each sinus drains into), and eight
bone cards cover maxilla and mandible in full plus the six smaller facial
bones. The importer gained a `cranium` scene that draws all fourteen skull
bones together — braincase and face, one colour per bone, opening on the
neurocranium card — so the whole skull turns and comes apart layer by layer.
Curriculum leaf `anatomy.musculoskeletal.skull.viscerocranium`, which the skull
topic already carried. The anatomy data is now 95 structures.

## Cranial nerves (7 September 2026)

After the neurocranium, the coverage audit's top gap was the cranial nerves —
the highest-yield head-and-neck topic and the one that hangs off the skull
foramina just added. The Academy now has an overview card whose table maps
each of the twelve nerves to its number, type, skull exit, function and lesion
sign, plus a card per nerve with components, nuclei, exit, course, motor and
sensory fields, parasympathetic branch, bedside test and clinical signs. The
exit on every card is one the skull base's foramen table already lists, and a
test keeps the two in agreement. The nerve card's fact order gained the
cranial-nerve fields as optional entries, so the limb nerve cards are
unchanged. Curriculum node `anatomy.neuroanatomy.cranial_nerves`. All curated
from the standard texts; the cards are schematic because no licensed nerve
mesh exists.

## One consistent voice (7 September 2026)

The student reported the spoken answers were unstable and hard to understand,
and wanted a single clear voice like ChatGPT's. The cause was a latency race:
free-tier cloud synthesis takes four to six seconds, the cloud voice's grace
window was three, so the instant but robotic local Windows voice usually won
and then carried the whole reply — a different voice almost every turn.

The voice now prefers the cloud neural voice: it always opens and carries the
reply, and the local Windows voice is a failure parachute only, never a faster
rival (`JARVIS_VOICE_PREFER_CLOUD_VOICE`, default on; set it off to restore the
race for a slow or metered link). Streaming synthesis keeps first audio around
one to two seconds, so consistency costs little latency.

The deeper limit is an account one, found by probing the live provider: the
free tier caps the speech-synthesis model at about ten calls a day
(`429 RESOURCE_EXHAUSTED`, `GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
limit 10 for `gemini-3.1-flash-tts`). Once spent, cloud synthesis is refused
for the rest of the day and the local voice speaks regardless of preference.
JARVIS now names that case: the quota refusal is tagged, and the conversation
shows "Bugünkü ücretsiz bulut ses kotası doldu… Gemini planını yükseltmen
gerekiyor." rather than a generic error. A stable natural voice every turn
needs billing enabled on the Gemini project; that is the user's to do.

Recognition, the engine turn, the microphone capture and the speaker output
were each verified working against the live provider; the instability was the
race plus the quota, not a broken stage.

## Black stage, free turn and the neurocranium (7 September 2026)

Three requests from the student, in one sitting. The lab's viewport is now
black in both themes, and the tissues are matte and saturated in the atlas
convention (bone ivory, cartilage cyan, muscle red-brown, arteries red, veins
blue, nerves yellow; a page test keeps every pair apart in RGB). The shader
lost its rim light and has no specular term; detail comes from a low ambient
floor and a grazing-angle darkening instead of a highlight. The model turns
about the viewer's own axes with no clamp (a rotation matrix accumulated by
the mouse, the keys and the pad, re-orthonormalised after each step) and
pans along the screen's axes.

While verifying the turn with a real drag, the reason the mouse had never
worked came out: the schematic map is an SVG, an SVG has no `hidden`
property, and `schematic.hidden = true` had hidden nothing — the transparent
map lay over the canvas and took every drag, click and wheel. The lab hides
it by attribute now; a page test forbids the property form, and the drag was
verified in a real Chromium with a real pointer.

The neurocranium has a section of its own: a region card written as a study
guide (calvaria and base, sutures and craniometric points, clinical
high-yield, how to study it) with two curated tables — the three cranial
fossae and a foramen-by-foramen list of what passes through — plus six bone
cards with eight to fifteen landmarks each, a curriculum topic
(`anatomy.musculoskeletal.skull`), and a second importer scene that draws
the eight bones from BodyParts3D, colours them one by one, hides them one by
one so the vault can be lifted off the base, and opens on the card. Pins are
derived by rule and marked approximate; a foramen gets no pin.

**Coverage audit** (asked for after the skull: "if the skull was missing,
other things may be"). Of the 28 anatomy leaf topics, the ones without a
structure card are the general topics served by the term glossary (planes,
movements, bone terms, bone structure, ossification, joint types, muscles and
fasciae) and two real gaps: the abdominal wall (no muscle cards) and the new
viscerocranium topic. By region and kind, the head and neck have skull bones
and one muscle but no cranial nerves, vessels or joints (temporomandibular,
atlanto-occipital); the trunk has two bones and four muscles, no vertebral
column cards beyond them, no joints, no vessels; the lower limb has bones,
joints, muscles and nerves but no vessels; the upper limb is the only region
with vessels. The other six subjects have 9–13 leaf topics each and no
structure cards by design. In priority order for a first year: the
viscerocranium bones (mandibula, maxilla, os zygomaticum), the cranial
nerves as nerve cards, the vertebral column and thoracic cage, the abdominal
wall muscles, then lower-limb vessels.

## Anatomy Lab in 3D (7 September 2026)

The lab now shows real geometry. `scripts/import_bodyparts3d.py` reads the
polygon-reduced BodyParts3D 4.0 archive (downloaded by the student; JARVIS
fetches nothing and no mesh is committed), maps 25 structures of the right
upper limb — bones, muscles, arteries and veins — from their FMA identifiers
to the archive's element files, merges each structure into one OBJ under the
state directory and writes the manifest entry with licence, attribution, side,
up axis and provenance. The structures form one scene, which the lab opens
with a layer chip per kind; clicking a mesh selects the structure. The view
turns, zooms and pans with the mouse, the keyboard and an on-screen pad. Six
vessel cards (axillary, brachial, radial and ulnar arteries; cephalic and
basilic veins) were added so every mesh has a card. Design in
`docs/MEDICAL_ACADEMY.md`.

Two honest limits. BodyParts3D 4.0 ships no bony landmark parts, so pins are
derived from the shape by a rule per landmark (the most proximal cap of the
humerus is its head) and written as approximate; the lab draws them with "≈",
a landmark no rule can place gets no pin, and a hand-placed pin outranks a
derived one on re-import. It ships no peripheral nerves of the arm either, so
nerves stay schematic.

Two study modes on top. *Detaylı* draws every pin with its Latin name on the
model; clicking one opens a card with the curated description and the pages
of the student's own library that mention it. *Zilli sınav* is the
bell-ringer: up to ten numbered stations from the scene's pins, one Latin name
typed per station, a fixed time (30–90 s) with a bell between stations and no
going back, the landmark list hidden while the exam runs, every station
recorded as an anatomy answer, and a results list with the correct name and
description.

Verified live in the Nova window: the scene with its four layers and the
attribution notice, picking, the pad and the keys, detailed pins with "≈", the
bell-ringer setup card, a station with the numbered pin and the timer, and
the results list.

## Medical Academy (6 September 2026)

`app/medical/` adds a first-year medical study layer for the seven subjects
of the curriculum: anatomy, histology, microbiology, biochemistry,
biophysics, physiology and medical biology. It is an extension of JARVIS,
not a second application — the same core engine, permission engine, approval
overlay, conversation store and Nova shell. Full design in
`docs/MEDICAL_ACADEMY.md`.

**How it reaches a turn.** `CoreEngine` gained one optional
`request_augmenter` (`app/core/augmentation.py`). Identity, clock and social
turns are answered by Core itself and never handed over; on any other turn
the domain layer may add to the system prompt, **narrow** (never widen) the
exposed tools, answer directly without a model call, or suppress
personal-memory writes. A broken augmenter is recorded in the ledger and
ignored. The academy's parser decides in about 0.5 ms whether a request is
medical at all; a plain "hava nasıl" is untouched.

**Data, not code.** 107 curriculum topics, 60 anatomical structures with 109
landmarks and 86 Latin terms (Terminologia Anatomica nomenclature), and about
200 learnable concepts with their relations, all in `app/medical/data/*.json`
and bundled with the frozen build. Latin stays Latin; Turkish explanations
follow it in the house format (`Tuberculum majus humeri — humerusun proksimal
ucundaki büyük tüberkül`).

**Documents.** PDF and text import (pypdfium2), deduplicated by digest,
copied below the study directory, extracted page by page with heading
detection, chunked with character offsets that map back into the page, and
indexed for BM25 search with synonym expansion — so "shoulder blade" finds a
Turkish passage about *scapula*. Figure-heavy pages are rendered and read by
the vision model, and the description becomes a searchable chunk. Progress is
reported as real stages, never as an invented percentage. Every citation
points at a chunk that exists; a page the model states but the evidence does
not contain is shown as unverified. `Compare with medical knowledge`
classifies lecture statements as consistent, simplified, incomplete,
potentially misleading, possibly incorrect or a terminology difference, each
with its page and what standard references say.

**Questions.** Generation is grounded and then filtered by deterministic
code: short stems, wrong option counts, duplicate options, "hepsi/hiçbiri",
an impossible answer key, an obviously longest correct option, a stem that
contains its own answer and a missing explanation are all rejected, as is any
near-copy of an existing question — including a reworded professor question
with the same answer. What was rejected is reported in the exam's notes.
Scoring, breakdowns, weak concepts and the next-step suggestion are computed
without a model.

**Professor style.** Imported exams are parsed deterministically (numbered
stems, lettered options inline or on their own lines, an inline answer, or a
trailing answer table). An answer key is never guessed: a question whose key
the text does not state is stored without one and shown as such. The profiler
measures fifteen observable features and reports each as `observed / total`
with a confidence that follows the sample size only; under ten questions it
says so in plain Turkish, and the generation directive repeats only ratios
that were actually observed.

**Learning.** Mastery is a readable rule (recent accuracy over the last eight
attempts), review intervals follow the level, every queued review says why,
and adaptive difficulty needs five recent results and moves at most one step.
Wrong choices are counted per concept, so an insight can name the actual
confusion instead of offering encouragement.

**Anatomy Lab.** Curated structure cards (bones, joints, muscles, nerves with
the documented field order), the relationship map, movement plane and axis,
and a deterministic landmark quiz. Geometry is never invented: a 3D mesh is
rendered in WebGL only when a licensed asset is registered in
`anatomy_assets/manifest.json` (an entry without a licence and a source is
refused); otherwise the lab says so and draws the schematic map.

**Latency.** The augmenter runs on every general turn, so its cost is the
assistant's cost. The first implementation compiled 1449 regular expressions
per request (115–150 ms in `find_in_text` alone, 130–170 ms per parse).
Aliases are now indexed by first token with every valid Turkish suffix strip,
patterns are compiled once, and the curriculum's and concept graph's token
sets are computed at load time: 0.06 ms for term recognition and 0.46–1.28 ms
for a full parse.

**Interface.** A twelfth Nova screen (Alt+3) with nine sections — panel,
konular, kütüphane, notlar, sınav, soru bankası, hoca tarzı, ilerleme,
Anatomi Lab — plus the study session controls, a page reader with the
rendered PDF page beside its text, the exam runner with flagging and a timer,
and the results screen with per-topic breakdowns and source links back to the
lecture page. Settings: `JARVIS_MEDICAL_*` (`docs/CONFIGURATION.md`).

**What the test pass changed.** 234 tests were added for the layers the first
round left thin (pipelines, exams, the tutor and the facade), and they found
three real defects, all now fixed with regression tests.

A "wrong answers only" exam padded itself from the bank when the student had
missed fewer questions than the paper needed, and still called the result
"yanlış yaptığın sorulardan oluşturuldu" — most of a paper labelled as the
student's own mistakes had never been one. `QuestionGenerator.from_bank` gained
`only_wrong`, and both callers (the exam facade and the chat quiz) now state the
count they actually found.

A bare number pair was treated on its own as a study scope, so ordinary system
requests — `sesi 20-40 arası ayarla`, `10-20 arası dosyaları sil` — were pulled
into the Academy and read as a PDF page range. A pair now marks a turn as
study-shaped only when the sentence says `sayfa`/`page`; inside an active study
context the bare form is still read as a scope, so `20-40 arası soru hazırla`
is unaffected.

The frozen smoke report gained `medical` and `medical_data` fields in the
previous session but the packaging test still described a complete report
without them, so the build gate was asserting against a shape it would have
rejected.

**The pre-merge review.** Before the branch was merged the whole layer went
through a twelve-lens adversarial review — fabricated sources, fabricated keys
and style, anatomy honesty, the engine seam, intent false positives, store
durability, the document pipeline, question quality, the learning model, the Nova
bridge, async and events, and the quality of the tests themselves. Forty-seven
candidate defects were raised; each was put to two independent attempts at
refutation (does it actually happen, and does it actually matter), and 28
survived. All 28 are fixed, each with a regression test, and each fix was audited
by an agent that had not written it.

The one that decided whether the feature worked at all: `CoreEngine` gives the
augmenter a two-second slice of the turn, and both the chat quiz and chat exam
generation awaited the provider inside it. With a provider answering in 1.5 s the
turn was already cancelled, and the shipped model answers in four to six — so
"beni sına" and "20 soru hazırla" silently did nothing in production: no quiz, no
exam, no error. Both now answer from the question bank when it can fill the
request and otherwise reply immediately and generate in the background, the way
document analysis already did, with the first question arriving in the
notification centre.

The rest, by promise. *Never fabricate*: a question cited the wrong document
whenever two documents shared a page number (citations are now resolved by the
excerpt index the prompt prints, and dropped when index and page disagree); a
second answer table overwrote the first paper's keys, a headerless key table
deleted the last question, and "Cevap D değildir" was stored as the key D; an
anatomy quiz offered a distractor that was itself a true answer; landmark labels
were projected in the asset's raw coordinates while the mesh was re-centred, so
each label pointed at the wrong part of the bone. *Do not break the rest of
JARVIS*: prefix matching claimed everyday Turkish words (genel, dizin, doküman,
kolay), "prof" matched profil, "exam" matched example, and an automation request
that merely named a medical thing lost every automation tool. *Latency*: saving
the study session bumped one global revision counter, so every study turn
rebuilt the whole BM25 index; invalidation now tracks only the writes the index
is built from.

Nineteen candidates did not survive refutation and were left alone, including a
duplicate of the index-rebuild finding and an unbounded page-image cache that
turned out to be bounded by the pipeline above it.

**The live run (6 September 2026, evening).** The desktop was started with the
configured Gemini key and the Academy was driven the way the student uses it —
the real window, real bridge, real provider. Three things no test had caught:

- Every structured model call failed with HTTP 400 `INVALID_ARGUMENT`. The
  deterministic paths were fine (a teaching turn augments in 0.1 ms, an ordinary
  request is left alone, the quiz turn answers at once), but the background
  generation behind it died, so the chat quiz, exam generation, document
  analysis and comparison all reported "Model çağrısı başarısız". A live bisect
  showed Gemini's OpenAI-compatible endpoint refusing a JSON schema whose nested
  arrays carry `minItems`/`maxItems`. `MedicalModelClient` now sends
  `wire_schema()` — structure, types, enums and required — and enforces every
  bound locally; the fake gateways in the tests accept anything, which is why
  the suite was green. After the fix a quiz was generated end to end in 12.5 s.
- The failure surfaces wired the day before worked: the toast, the notification
  centre entry and the `job.failed` ledger warning all appeared for the broken
  call, which is how it was found.
- A quiz question written for the chat carried its Markdown into the
  notification centre (`**Soru 1**` as literal asterisks); notification bodies
  are plain text now.

The same session changed what a quiz is. Ali asked for papers he can mark, a
finish button, and then his wrong answers explained with the correct option,
the topics he has gaps in, and questions with figures. A typed "beni sına" now
builds a short paper (answers at the end) and opens it on the exam screen; the
results put the wrong answers first with their explanation and the correct
option, then the blanks, then the correct ones, over the topic, difficulty and
subject breakdowns and the weak-concept chips; a generated item on a
subject-only paper is filed under the topic of its concept so the topic
breakdown says something; and "görselli sorular" draws figure questions from the
lecture pages the vision pass described, showing the rendered page beside the
stem — JARVIS never draws anatomy of its own. The letter-by-letter chat quiz
remains for the voice loop. Verified in the real window: "Bu konudan sına" opened
the paper in four seconds, two answers were marked, the paper was finished, and
the results screen showed the wrong answers, the blanks and four named weak
concepts.

## Persistent notification centre (5 September 2026)

Routines now run unattended, so what they reported must still be there
after the desktop restarts. `NotificationCenter` takes an optional
`NotificationStore` (SQLite below the state directory,
`JARVIS_NOTIFICATIONS_DATABASE_PATH`, empty disables it): every publish,
collapse, mark-read, dismiss and clear is written through, the newest 200
entries are loaded when the bridge starts (ties in the timestamp broken by
insertion order), and pruning keeps the file at the same bound. Storage
errors are reported, never raised: a full disk must not stop a reminder
from being shown. Sessions without a path (tests, demo) stay in memory;
`NotificationCenter.persistent` says which.

## Clock in the prompt and fresh page assets (5 September 2026)

Two live findings. Asked "saat kaç" the model invented a time and asked
"bugün günlerden ne" it named the wrong day: nothing told it the clock.
Every system prompt now ends with the machine's local date and time in
Turkish (`Şu an yerel tarih ve saat: 5 Eylül 2026 Cumartesi, 17:10.`).
The lite model with sixty tool schemas in front of it still invented the
time with that line present, so plain clock questions (`saat kaç`, `bugün
günlerden ne`, `bugünün tarihi`, `what time is it`; at most nine words)
are answered by the interaction policy directly from the machine's clock,
without a model call at all: instant and never wrong. Longer requests that
merely mention the time (`saat 9'da hatırlat`) go to the model as before.
Second, the WebView2 profile keeps an HTTP cache for the page's own
file:// scripts and styles, so an updated build could keep running the
previous page; `launch_nova` now stamps the asset tree (paths, sizes,
mtimes) and, when the stamp differs from the last launch, clears the
profile's `Cache` and `Code Cache` before the window exists, recording
`webview.cache_cleared` in the ledger. An unchanged build keeps its warm
cache; theme and motion preferences are untouched.

## Scheduled routines (5 September 2026)

`app/routines/` adds `RoutineService`: a persistent, bounded (20) store of
named prompts with a schedule, daily at a local clock time (`her gün 09:00`)
or every N minutes (5 minutes to a week). Due routines are claimed
atomically: the claim moves `next_run_at` to the following occurrence
before the routine is handed out, so a run happens once even across a
restart. The model gets `create_routine`, `list_routines` and
`delete_routine` (the schema selector exposes them for phrases such as
"her sabah 09:00'da ...", "rutinlerimi listele", "... rutinini sil";
"her gün ... hatırlat" stays a reminder).

The Nova bridge polls the store every 30 s with the same daemon watch that
delivers reminders and runs a due routine through the core exactly like a
typed command: same engine, permission engine and approval overlay (an
approval nobody answers fails closed), `RequestSource.SYSTEM`, in the
routine's own conversation which persists between runs. The outcome lands
in the notification centre as `Rutin · <ad>` with the reply text, reaches
the OS when the window is unattended, and opens the routine's conversation
when clicked; failures are reported by exception class only. A paused or
busy desktop defers a due routine by 90 s instead of dropping it. The
Tasks screen lists routines (schedule, next run, last outcome, run count)
with a confirmed delete and an editor (name, daily time or interval,
command) that goes through the same bounded service call and validation
messages as the `create_routine` tool; ledger events `routine.created`,
`routine.started`, `routine.completed`, `routine.deferred`,
`routine.deleted`. Setting:
`JARVIS_ROUTINES_DATABASE_PATH`. Routines execute nothing themselves and
never bypass a gate a typed command would face.

## Faster start-up (5 September 2026)

Profiled in-process from the first import to the bridge's `boot()`: 4.3 s,
of which `create_application` spent 1.8 s building two google-genai speech
clients (each loads four SSL trust stores and the SDK import costs as much
again) and `import openai` cost 0.9 s before the window could open. Now the
recognizer and synthesizer share one google-genai client per key that is
built on first use, the OpenAI-compatible provider builds its client (and
imports the SDK) on first use, and the boot warm-up builds all of them on
the runner loop while the boot animation plays: the same measurement is
0.78 s, neither SDK is imported when the page boots, and the warm-up ledger
line reports `gemini`, `voice_stt` and `voice_tts` warm about three seconds
later. Injected clients (tests, connection checks) behave as before, and
`is_configured` still answers from the key, not from the client object.

## Voice time to first audio (5 September 2026)

Measured without a microphone (a local voice spoke a Turkish command into
a WAV, the real recognizer and synthesizer handled it): transcription on
`gemini-3.5-flash-lite` 1.0-1.5 s for a 3 s clip; `gemini-3.1-flash-tts-preview`
2.8-3.4 s for one sentence as a whole response but its first streamed
audio chunk after 1.2-1.3 s; the local Windows voice 0.8 s. The reply
itself now arrives in about 1 s (previous section), so speech synthesis
had become the largest fixed cost between the user's last word and the
first sound.

- `GeminiSpeechSynthesizer.synthesize_stream` returns a `SpeechStream`: PCM
  chunks as Gemini produces them, primed on the first chunk (that is the
  moment audio exists and the real sample rate is known), bounded by the
  same size limit; a client without an async streaming surface yields the
  whole clip as one chunk, so callers have one code path.
- `AudioOutput.play_stream` plays such a stream. The Windows output writes
  chunks to the speakers through sounddevice from a worker thread as they
  arrive, stops within the chunk in flight on interruption and reports
  device failures like `play`; the base class buffers the chunks into one
  WAV for outputs that cannot stream.
- The voice session races the cloud's first audio chunk (not the whole
  sentence) against the local voice under the existing grace rule, plays
  the winner as a stream when it is one, and listens to the streamed reply:
  as soon as the model has moved past the first sentence (a terminator
  followed by more text, at least two words) that sentence is sent to
  speech while the rest is still being written. When the final reply text
  does not start with that sentence (a guard rewrote it) the early speech
  is discarded and never heard. Engines that do not accept a stream
  callback keep the previous behaviour. New metadata:
  `first_sentence_early`, `first_audio_latency_seconds`.
- Transcription starts during the trailing silence. The microphone offers
  the audio heard so far as a provisional capture once
  `JARVIS_VOICE_PROVISIONAL_SILENCE_SECONDS` (0.6 s) of silence has passed,
  the session transcribes it at once, and when the capture then ends
  without new speech (the final audio adds no more than the trailing
  silence to the provisional prefix) that transcript is the turn's; speech
  that resumes discards it and a fresh offer follows. With the user-tuned
  1.5 s trailing silence this hides most of the ~1 s transcription; both
  silences are shown read-only under Ayarlar › Ses. Inputs
  without the callback behave as before; metadata
  `transcription_provisional` says which path a turn took.
- The speech adapters warm their client at Nova boot together with the
  model gateway (one model description fetch each, no generation quota);
  the `provider.warm_up` ledger line reports `voice_stt` and `voice_tts`
  next to `gemini`.

Live on this host: a streamed sentence through the real Windows output
started playing 1.30 s after the request where the whole-clip path waited
about 3 s. A spoken turn at the microphone remains a user-attended check.

## Gemini 3 tool turns and latency (5 September 2026)

A measured pass over a real turn (the desktop's own settings and key, the
core engine, timed provider calls) found and fixed four things.

- **Signed replays.** Gemini 3 models attach a thought signature to every
  function call and reject a follow-up whose replayed function call has
  none (HTTP 400). Model-made calls already kept the signature; calls the
  core injects itself (deterministic routes such as `sistem bilgisi`) and
  calls stored before signatures were kept did not, so those turns failed
  outright. `GeminiProvider` now replays every unsigned function call with
  Google's documented skip marker; the stored conversation is untouched.
- **Streaming on tool turns.** The chat-only streaming route used to refuse
  tool calls, so any turn that exposed tools waited for the complete
  answer. The collector now folds streamed tool-call deltas into whole
  calls (index- or id-keyed, argument fragments appended, signatures kept),
  and the engine streams whenever the caller listens and the default
  provider can stream. The final answer after a tool result appears as it
  is generated, and that call asks the gateway for the `simple` task type
  (minimal reasoning) explicitly, since the gateway classifies reasoning
  itself and request metadata alone changed nothing.
- **No default escalation.** Tool-bearing turns escalated to
  `gemini-3.7-flash`, whose free-tier quota is 20 requests per day, and the
  next candidate `gemini-3.5-flash` measured 3.7-5.9 s per tool call on this
  tier where `gemini-3.5-flash-lite` took 0.6 s and chose the same tool.
  `JARVIS_GEMINI_ACTION_MODEL` is now empty by default (escalation is
  opt-in). When an action model is configured and reports a quota error,
  the gateway makes a single attempt (no backoff, no retry), the default
  model answers at once, and the action model rests for a cooldown that
  starts at the reported wait or 60 s and doubles on repeat up to an hour;
  the wait is read from the response body when no Retry-After header is
  present.
- **Warm-up and visibility.** The Nova bridge warms the provider connection
  on the runner loop at boot (a model listing, no generation quota), so the
  first command does not pay DNS, TLS and client set-up. Every provider
  round trip is a `request.model_call` ledger event (iteration, model,
  streamed, tool count, task type, reasoning level, latency, first output)
  with `core.model.latency` and `core.model.first_output` timers, and
  fallbacks carry their reason (`rate_limited`, `cooldown`).

Every assistant reply in Nova now carries the core's own numbers as chips:
the turn's elapsed seconds (`1,1 sn`) and, when tools ran, their count
(`araç · 1`); both come from the response metadata, are persisted with the
turn so a reopened conversation shows them too, and are simply absent when
the core did not report them.

Measured on this host with the same seven requests before and after
(streamed first output in parentheses): a plain question with all tools
exposed 5.25 s to 0.88 s; `hatırlatıcılarımı listele` (one tool, two model
calls) 12.24 s to 1.31 s; `bilgisayarımın sistem bilgisini göster`
(deterministic route, previously a 400 error) 1.08 s; a general question
11.94 s to 1.11 s; the voice-source greeting 0.95 s. The first turn of a
process still carries about 0.8 s of connection set-up, which the boot
warm-up moves off the user's first command. Tool selection quality on the
lite model held for every probe; a heavier action model remains one
environment variable away.

## Nova notification centre (5 September 2026)

Before this milestone the Nova shell never delivered reminders: the
delivery loop lived in the classic Tk window only, so a reminder created
from Nova was stored and never shown. `app/notifications/` adds two small,
UI-independent pieces. `NotificationCenter` is a bounded (200 entries),
thread-safe list of things that deserve attention (persisted below the
state directory since the evening of 5 September 2026); every
entry has a kind (`reminder`, `approval`, `reply`, `task`, `diagnostic`,
`observation`, `system`), a Turkish title, a body bounded to 600
characters, a severity, an optional target screen and a small data map,
and repeats with the same dedupe key within 60 seconds collapse into one
entry with a count. `ReminderWatch` polls `ReminderService.claim_due()` on
a daemon thread (first poll immediately, then every 10 s), so each due
reminder fires exactly once and reminders that came due while the desktop
was closed fire right after boot.

`NovaBridge` owns one centre per window session and feeds it from the
running core: due reminders; ledger events at warning level or above
(collapsed per component and name, target `Tanılama`); screen-watcher
observations (target `Görüş`); and, only while the window is unattended,
assistant replies (target `Sohbet`), approval requests (tool name and risk
only, never parameters) and finished vision or research work. The page
reports `document.visibilityState` through `set_visible`, the tray's
open/hide and pywebview's minimized/restored events set the same flag, and
for alert-worthy entries on an unattended window the bridge also raises a
native notification on its own thread (tray balloon when the icon exists,
otherwise the WinRT toast, at most four in flight); the ledger's warnings
never reach the OS. `JARVIS_NOTIFICATIONS_OS_ENABLED=false` keeps native
notifications off; the in-window centre is always on. Both decisions are
observable in the ledger: `window.visibility` (attended true/false) and
`notification.native` (delivered, channel `tray` or `toast`, title only;
the body is user content and stays out).

The page gains a bell in the top bar with the live unread count, a
popover (`Ctrl+Shift+N`, palette entry, outside click and Escape close it)
listing entries newest first with kind icon, relative time and repeat
count, per-entry open (marks read, jumps to the target screen) and dismiss,
`Tümünü okundu say` and `Temizle`, an in-page toast for each new entry, and
a system line in the conversation for reminders and screen observations as
the classic shell shows. Pushes that arrive before boot finishes are merged
after it, so a reminder due at start-up is not lost. New bridge methods:
`list_notifications`, `mark_notifications_read`, `dismiss_notification`,
`clear_notifications`, `set_visible`; new push kind `notification`.

Tests: `tests/test_notifications.py` (centre and watch) and new cases in
`tests/test_ui_nova.py`, `tests/test_nova_web.py`, `tests/test_settings.py`.
Live on this host from the Nova shell: a Gemini command created a
one-minute reminder; when it came due the bell showed `1`, the popover
listed it (`Ctrl+Shift+N`) and the conversation gained the `⏰ Hatırlatıcı`
line. A second reminder was set, the maximized window was minimized, and
the ledger recorded `window.visibility attended=false`, then
`notification.native delivered=true via=tray`, then `attended=true` when
the window came back with the badge at `1`; the same run exposed and fixed
the `maximized` event gap. Whether Windows painted the balloon could not be
observed because a full-screen video was on the desktop at the time
(Windows suppresses notifications then); the bridge-side path is covered
by the unit tests with a fake notifier. The frozen build was not rebuilt
for this milestone.

## Safe-filesystem extensions (5 September 2026)

`app/platform/windows/snapshots.py` adds `FilesystemSnapshotStore`: before a
bounded tool replaces or removes a file, its exact bytes are sealed below the
state directory (`filesystem_snapshots/<id>.bin` + `<id>.json` manifest with
root, relative path, size, SHA-256, reason, time and tool). Payloads are only
handed back after the digest and size are re-verified, the store is bounded
by count and total bytes (`JARVIS_FILESYSTEM_SNAPSHOT_MAX_ENTRIES`,
`JARVIS_FILESYSTEM_SNAPSHOT_MAX_BYTES`; oldest pruned first), and a file the
store cannot hold blocks the mutation instead of proceeding unprotected.

`BoundedFilesystemService` gains, all under the same root-and-relative-path
policy and the same confirmation gates:

- `delete_path` (HIGH, confirmation) — a real, recoverable delete: files are
  sealed then unlinked, only empty directories are removed, links are
  refused; without a snapshot store a file delete stays blocked.
- `undo_filesystem_change` (HIGH, confirmation) — writes a snapshot back to
  its original path; the file currently there is sealed first, so an undo
  can itself be undone. `list_filesystem_snapshots` is read-only.
- `write_text_file`, `copy_file` and `move_file` with `overwrite=True` seal
  the file they replace and report the snapshot.
- `search_files` (read-only) — a bounded name index per root (20 000
  entries, refreshed after 60 s or on demand), case-insensitive substring or
  glob, optional sub-path scope; links and reparse points are listed but
  never followed.
- `plan_filesystem_changes` (read-only dry run of up to 50
  write/create_directory/copy/move/delete operations under one root:
  reports per-operation readiness and conflicts, touches nothing) and
  `apply_filesystem_plan(plan_id, digest)` (HIGH, confirmation): the plan is
  single-use, expires after ten minutes, its digest must match, every target
  is re-checked against the fingerprint taken at planning time
  (`PLAN_TARGETS_CHANGED` otherwise), and execution stops at the first
  failure reporting what was applied and its snapshots.
- Critical directories can never be granted: Windows, Program Files,
  ProgramData, the JARVIS state directory, the user profile root itself (its
  subfolders remain grantable), `$Recycle.Bin` and `System Volume
  Information`; the snapshot store cannot sit inside a granted root.

The Nova settings screen gains "Dosyalar": granted roots with add (native
folder picker on the UI thread, then an in-page confirmation) and remove, and
the snapshot list with restore (confirmed in the page, `confirmed=True` on
the bridge). Grants, revocations and restores are recorded in the ledger.

Verification: `tests/test_filesystem_snapshots.py`,
`tests/test_filesystem_recovery.py`, the updated
`tests/test_bounded_filesystem.py` and the bridge tests in
`tests/test_ui_nova.py`. Live on this host from the Nova shell: a test
folder granted through the native picker and the in-page confirmation, a
Gemini command that created `deneme.txt` after the `write_text_file`
approval (content verified on disk), a second command that removed it
after the `delete_path` approval (HIGH risk shown, file gone, one sealed
snapshot in the store), the snapshot listed under Ayarlar › Dosyalar and
restored from there with its confirmation (original bytes back on disk),
and the grant removed again. The frozen build was rebuilt on 5 September 2026
(evening, release qualified) with every change merged that day, through
the last code change (the voice silence settings on the settings screen).

## Nova cinematic interface (5 September 2026)

The Nova page was rebuilt as a design system rather than a single file:
`web/css/tokens.css` (colour, surface, radius, glow, type, motion and z-index
tokens), `base.css`, `shell.css`, `components.css`, `screens.css`, and eight
scripts under `web/js/` (`foundation`, `bridge`, `presence`, `shell`,
`conversation`, `activity`, `panels`, `main`). `shell.WEB_ASSETS` names every
file, the PyInstaller spec collects the `web/` directory recursively, and the
build script imports the list from the shell so the smoke report cannot drift.

- **Presence.** One state machine (`presence.js`) decides what JARVIS is
  doing — offline, idle, listening, understanding, thinking, tool, waiting for
  permission, speaking, interrupted, paused, error — from the bridge status,
  voice phases, live tool activity and open approvals. The topbar readout, the
  captions, the tray-paused state and the core visualization all read it.
- **JarvisCore.** An original canvas visualization: index ring, segmented arcs
  with travelling highlights, inclined orbits with satellites, radial data
  spokes that carry light while computing (biased toward the context panel
  while a tool runs), an interference field driven by the real microphone
  level while listening, and a breathing nucleus with a speech halo. It runs
  at the monitor refresh rate, drops to 30 fps when calm, pauses when hidden,
  and draws one frame under "Hareketi azalt".
- **Real data only.** Tool activity is observed through
  `ToolExecutor.subscribe` (started/finished with status, verification and
  duration), live events through `DiagnosticsService.subscribe`, the
  microphone level through `VoiceService.level_callback`. The diagnostics
  screen shows real health checks, the metric registry, admission and provider
  circuit figures, process memory/threads/uptime (CPU after the second
  sample), runtime versions and the ledger tail; anything unmeasured is shown
  as "kullanılamıyor". Boot lines report the actual subsystem state.
- **Screens.** Command centre (greeting, core, quick command, quick actions,
  recent activity, system rows), conversation (integrated JARVIS messages,
  streaming, inline activity strip, assurance chips, stored-conversation list
  with open/new/archive), tasks (step timelines), memory (grouped by type,
  search, edit, forget, permanent delete with confirmation, privacy note),
  voice (inline core, level bar, transcript; full-screen stage with phase
  readout and captions), vision, research, automation (tools grouped by
  source), trust (risk distribution, session approvals, the permission
  engine's own audit trail), diagnostics, settings (connection, appearance,
  read-only runtime configuration per subsystem, shortcuts).
- **Interaction.** Command palette (Ctrl+K: screens, actions, real approved
  applications, "JARVIS'e sor"), contextual drawer that opens itself during
  execution and settles afterwards, collapsible rail, compact always-on-top
  window (`set_compact`, geometry applied on the WinForms UI thread), in-page
  pause/resume through the same path as the tray, tray "Sesli mod" entry.
- **Honesty and safety.** The approval overlay offers only "Bir kez izin
  ver" and "Reddet" — no blanket permission exists; it shows the tool in
  Turkish, the raw tool name, the operation, the permission engine's reason,
  the request source, the expected effect and the masked parameters. Secrets
  never cross the bridge; runtime configuration is exported through an
  explicit allow-list. Memory deletion needs `confirmed=True`. Observers are
  read-only and cannot change a tool's outcome.

Verified live on this host (source, Windows 11, 1920×1080 at 125 %): boot
with real subsystem lines, every screen, palette navigation and application
launch entries, a real Gemini command that requested permission for
`launch_windows_application` (overlay shown with Turkish labels), the denial
producing the reply plus the inline "Reddedildi" pill and the drawer timeline
(İstek alındı → Anlaşılıyor → İzin istendi → Yanıt yazılıyor → Tamamlandı),
compact mode entering at the bottom-right and expanding back, the
diagnostics, memory and settings screens with live data, the tray menu
showing `Sesli mod`, and `Çıkış` ending the process cleanly. Ctrl+M opened
the voice stage with the real pipeline: the stage moved through
`DİNLİYOR` and `KONUŞUYOR` as the microphone picked up ambient speech and
JARVIS answered aloud, and Escape ended the session; a deliberate spoken
exchange still needs a person at the microphone. The demo page (`?demo=1`)
was used for task timelines, memory cards and the approval overlay in a
browser. The frozen build was rebuilt after the redesign and its smoke
report lists every page file as `nova_assets`.

Known limitations: tool descriptions and a few permission reasons that the
core registers in English are shown as-is when no Turkish mapping exists; the
compact window assumes the primary monitor; "always allow" is intentionally
absent.

## System tray and single instance (5 September 2026)

`app/ui/tray/` adds the notification-area icon for the Nova shell: `Aç`
(`Öne getir` while visible), `Duraklat`/`Devam`, `Tanılama`, `Ayarlar`, and
`Çıkış`, plus double-click to open. The icon is a WinForms `NotifyIcon` on
its own STA thread (pythonnet is already present through pywebview; no new
dependency). Closing the window hides it to the tray with a one-time
balloon; `Çıkış` runs the ordinary clean shutdown. `Duraklat` is a UI gate:
the controller refuses new commands, voice, vision, and research, stops an
active voice session, and the page shows `DURAKLATILDI`; reminders and other
background services keep running. A named mutex and event keep one desktop
per user session: a second launch activates the first and exits. A tray that
cannot start is recorded as `tray.error` and the window runs without it.
Settings: `JARVIS_TRAY_ENABLED`, `JARVIS_TRAY_CLOSE_TO_TRAY`,
`JARVIS_SINGLE_INSTANCE` (all default true). The classic Tk shell has no
tray icon (it still honours the single-instance guard).

Verified live on this host: icon in the notification area with the JARVIS
logo, balloon on close-to-tray, `Aç` restoring the window, `Duraklat`
refusing a command with the Turkish notice and `Devam` restoring it,
`Tanılama` opening that screen, `Çıkış` ending the process cleanly, and a
second launch bringing the first window forward.

## Plugin runtime v1 (5 September 2026)

`app/plugins/` adds manifest-based plugins that contribute tools through the
existing `ToolExecutor` and `PermissionEngine`; nothing is bypassed and no
core contract changed. Core touches are limited to four settings
(`plugins_enabled`, `plugins_directory`, `plugin_tool_timeout_seconds`,
`plugin_max_consecutive_failures`) and the bootstrap wiring
(`JARVISApplication.plugins`, stopped on `close()`).

- Discovery is confined to immediate subdirectories of the trusted plugins
  root, never follows links or junctions, bounds manifest size, and imports
  no code. Manifests are versioned, closed-schema, and fail closed.
- Plugins are disabled by default; enabling is an explicit, persisted user
  decision. Plugin tools are namespaced (`plugin_<id>_<tool>`), carry
  `source="plugin:<id>"`, sit at or above the `LOW` risk floor, and require
  approval when medium or high. Results are unverified by contract.
- Isolation: per-call deadline on a bounded worker pool, JSON-only bounded
  output, class-name-only error reporting, and quarantine after consecutive
  failures with explicit re-enable. Plugin code receives only its id,
  version, a private data directory, and a bounded ledger logger.
- Honest limit: in-process Python cannot be sandboxed; a plugin is trusted
  code the user installed and enabled. Process isolation, a settings UI,
  and signing are later versions.
- Tests: `tests/test_plugin_manifest.py`, `test_plugin_discovery.py`,
  `test_plugin_runtime.py`, `test_plugin_security.py`,
  `test_plugin_bootstrap.py` with the safe `tests/fixtures/plugins/echo`
  sample.


Nova (`app/ui/nova/`) is now the default desktop shell: a pywebview window
hosting `web/index.html` in Microsoft Edge WebView2, with every animation on
the browser compositor and every fact coming from the Python core through the
`NovaBridge` JS API and the `window.NOVA.push(...)` channel. The Tkinter shell
remains available with `python -m app.ui --classic` (or `JARVIS.exe --classic`)
and is used automatically when pywebview is not installed.

Stabilization changes, all covered by tests:

- **No silent demo.** The page previously fell back to sample data when the
  Python bridge did not answer within 1.6 s. It now waits up to 10 s for
  `pywebviewready` and, on failure, shows an explicit "çekirdek köprüsü
  kurulamadı" screen with a retry button; nothing is simulated. The demo bridge
  runs only when the page is opened directly in a browser with `?demo=1`, never
  inside pywebview, and is labelled DEMO in the top bar, the boot log, every
  reply, and a persistent toast.
- **Frozen asset resolution.** `resolve_web_root()` looks below
  `sys._MEIPASS/app/ui/nova/web` first, then the source tree, and reports a
  missing file set instead of opening an empty window. `installer/JARVIS.spec`
  bundles the three page files at that path, the frozen smoke test records them
  as `nova_assets`, and `scripts/build_windows.py` refuses a report without all
  three.
- **Race-free bridge.** The busy check and the submission share one lock, so a
  double send cannot slip past the guard; voice start/stop and shutdown are
  guarded the same way. Approval requests are single-use tokens that fail
  closed on timeout, on shutdown, on a non-boolean answer, and when the page is
  not ready. Window close releases the bridge, the async runner, and the
  application exactly once, whether the `closed` event fires or `webview.start`
  simply returns. The bridge lock is re-entrant: when a background task
  finishes before its completion callback is registered, concurrent.futures
  runs that callback synchronously on the submitting thread, which used to
  deadlock the pywebview worker (found as an intermittent test hang, fixed
  with a deterministic regression test); shutdown acquires the lock with a
  deadline so it can never hang on a stuck worker.
- **Credential deletion needs two explicit steps.** The Settings screen opens
  an in-app confirmation dialog (separate from the tool-approval modal) and the
  bridge ignores `delete_api_key()` unless it is called with `confirmed=True`.
- **Persistent WebView2 profile.** The window runs with `private_mode=False`
  and `storage_path=%LOCALAPPDATA%\JARVIS\webview`, so the theme and the
  in-app "Hareketi azalt" switch survive restarts (verified live). The OS-wide
  `prefers-reduced-motion` setting is deliberately not inherited.
- **Reading is never interrupted.** Chat auto-scrolls only when the reader is
  already at the bottom; otherwise a "yeni mesaj" pill appears. Arrow, Page,
  Home, and End keys scroll the active screen when no input has focus, and a
  send attempted while JARVIS is busy keeps the draft and says so.
- **Runtime detection.** `detect_webview2_runtime()` asks Microsoft's
  WebView2Loader (bundled with pywebview) and then the Evergreen registry
  entries before any window exists; without a runtime `launch_desktop`
  records a warning event, posts a Turkish system message, and opens the
  classic shell instead of crashing out of pywebview.
- **Packaged entry point.** `JARVIS.exe --classic` is accepted, and the
  `.venv` is now expected to be re-synchronized from `requirements-dev.txt`
  whenever `pyproject.toml` changes (`pip check` cannot detect a dependency
  that was declared but never installed).
- **Tests.** `tests/test_ui_nova.py` (34 tests) drives `NovaBridge` against the
  real controller with a recording window; `tests/test_nova_web.py` (18 tests)
  parses `nova.js` with QuickJS, checks that every JavaScript bridge call
  matches a Python method and arity, that the demo bridge mirrors the Python
  API, that demo mode is opt-in only, and that the page declares the failure
  and confirmation UI. The packaging tests require the Nova assets in the spec
  and in the smoke report. `scripts/verify.py`: 1197 passed, 1 skipped.

### Verified live on this host (5 September 2026)

Source launch (`python -m app.ui`, real Gemini credential from Credential
Manager, WebView2 Runtime 152): boot into Nova; every screen reachable by rail
click and Alt+digit; mouse-wheel, PageUp/PageDown, Home/End scrolling; a text
command answered by the core (about 10 s round trip, tool-verified time
query); a reply arriving while scrolled up left the view in place and showed
the pill; Ctrl+M opened the text-free full-screen HUD and, with no speech, the
session closed itself with the honest "Ses algılanmadığı için sesli modu
kapattım" notice; Vision and Research (both disabled in this configuration)
failed closed with visible messages; a clipboard write raised the ORTA
approval modal with masked parameters, was denied, produced "İşlem iptal
edildi; bilgisayarında değişiklik yapılmadı", and left the clipboard
untouched; the Settings connection test succeeded against the live model; the
delete-key dialog was cancelled with Escape; the motion switch persisted
across a restart; Alt+F4 left no process behind and an empty stderr. The
frozen `JARVIS.exe` from the rebuilt package also opened Nova and closed
cleanly.

Not re-qualified in this pass: a spoken voice turn (cloud Charon speech and
the local Windows fallback). The host has no microphone input JARVIS could be
driven with unattended, so that path keeps its 23 August qualification from
the classic shell and still needs a run with the user's own microphone.

## Provider consolidation (22 August 2026)

JARVIS now ships exactly one production AI provider: **Gemini**, reached through
Google's OpenAI-compatible API surface.

- Removed: the Ollama provider, its warm keeper, its hybrid chat/tool routing
  policy, and the OpenAI speech adapters. `app/providers/openai.py` remains only
  as the shared adapter base class that `GeminiProvider` extends.
- `MockProvider` remains the deterministic offline provider for the automated
  suite. `create_application()` registers it, and makes it the default, only
  when `settings.default_provider == "mock"`. The desktop cannot select it.
- Provider fallback is disabled by construction. With one production provider
  there is nothing to fall back to, and falling back to the mock provider would
  replace a real failure with a convincing fiction.
- `DeterministicToolRouter` is now provider-neutral. It previously refused to
  route unless the active provider was Ollama, which silently disabled the
  latency optimization after the migration. Every candidate it routes to is a
  `READ_ONLY` observation tool and the permission engine still authorizes the
  call, so removing the provider gate removes a model round trip, never a
  security boundary.
- `vision_model` is now an optional dedicated Gemini vision model instead of a
  dead `gpt-4o` default. When set and different from the general model, `VISION`
  requests route to it exclusively.
- Dead configuration knobs were removed rather than left to mislead:
  `voice_stt_model`, `voice_tts_model`, `voice_tts_voice`, and
  `provider_fallback_enabled` had no remaining consumer, and the retired
  `JARVIS_API_KEY`, `JARVIS_API_BASE_URL`, and `JARVIS_OPENAI_MODEL`
  environment variables are no longer read.
- The approved-application fast-action router, previously gated behind the
  Ollama hybrid mode, now activates whenever Windows integrations are enabled.
  It short-circuits only registered application launches, and the launched
  process is still verified by PID and identity.
- Stale environment from older builds cannot break startup: unknown
  `JARVIS_DEFAULT_PROVIDER` values fall back to Gemini, the desktop ignores
  stale default-model and voice-provider variables, the retired `gpt-4o`
  vision default is dropped, and voice automatic selection falls back to
  Gemini when the text provider has no speech adapters.

### Verified on this host

- Native Tcl/Tk 8.6.15 initializes, and all eleven desktop screens render from
  source on a normal Windows 11 account. The sandbox limitation recorded in the
  Phase 13 and Phase 17 notes below does not apply to this machine.
- `scripts/verify.py` passes dependency integrity, bytecode compilation, and the
  complete deterministic suite.
- Voice was qualified live on 23 August with the real desktop, real
  Gemini APIs, and a scripted microphone: capture, 1.3s transcription,
  short-reasoned reply, sentence-pipelined speech, HUD states, earcons,
  and a graceful, explained silence-close. When cloud synthesis is
  rate-limited the turn retries once and then answers through the local
  Windows SAPI voice with an honest notice — a voice turn can no longer
  end silently.
- The full release pipeline ran end to end on 22 August: PyInstaller build,
  frozen smoke test (`ok=true`, `screens=11`, `tcl=8.6.15`), portable ZIP,
  Inno Setup installer compile, silent clean install, installed-location smoke
  test, in-place reinstall, and silent uninstall with user data preserved.

## Application integrations and live awareness (23 August 2026)

JARVIS now drives real applications and watches the screen. Every
capability is a permission-checked tool; nothing bypasses the approval
gate or the verification contract.

- **Spotify**: transport control through media keys verified against the
  window title, search deep links, and an optional Web API tier behind
  one-time PKCE OAuth for exact playback, private playlist creation, and
  listening statistics.
- **WhatsApp**: contact book, deep links that prefill without sending,
  chat and conversation reading through the native UIA3 accessibility
  tree, HIGH-risk approval-gated sending that reports PARTIAL when the
  send button cannot be verified, and a bounded delegation agent that
  answers a named contact on the user's behalf with draft screening for
  credentials and commitments.
- **System**: http/https-only browser navigation, web search, volume.
- **Reminders**: persistent SQLite reminders with exactly-once delivery
  and native toast notifications.
- **Screen watching**: continuous observation with local 12x12 luminance
  change detection (0.11ms per frame) that calls the vision model only
  on real change; frames are discarded immediately after signature.

Hardening from live use on 23 August 2026: Spotify plays a named
track with no account setup by driving the desktop app's own search
UI (verified against the window title); WhatsApp launches itself when
closed and opens chats by their visible list name with an empty
contact book; every chat render lands at the newest message instead of
the top; and PowerShell output is forced to UTF-8 so Turkish titles
survive.

Intent handling was hardened alongside: unresolved phrasings expose the
full tool inventory instead of failing closed, tool-bearing turns
escalate to the stronger action model with a graceful rate-limit
fallback, and an action-integrity directive forbids claiming an action
without calling its tool.

Local Turkish speech now goes through WinRT ("Microsoft Tolga"), which
SAPI does not expose, and races cloud synthesis so the reply starts with
whichever source answers first.

## Voice quality and integration robustness (23 August 2026, session 2)

- The first spoken sentence now gives the high-quality cloud voice a
  bounded head start (`voice_cloud_grace_seconds`, default 3.0s):
  within the window the cloud voice wins even when the instant local
  voice finished first, so the robotic Windows voice is heard only
  during real outages or past-deadline slowness. 0 restores the pure
  latency race.
- Every synthesis request carries a JARVIS persona style directive
  (`voice_tts_instructions` now defaults on), and the local fallback
  is bilingual: replies without Turkish letters or everyday Turkish
  words are spoken by the English Windows voice instead of Tolga
  spelling English out phonetically.
- End-of-turn silence is 1.5s (was 0.9s), per user tuning.
- Spotify `play_track` works with zero account setup: when no Web API
  token exists (or no active device is registered) it drives the
  desktop app itself — search deep link, then the top result's play
  button through UI Automation — and verifies via the window title.
  Verified live: both an artist query and a specific-song query.
- WhatsApp launches itself when closed, and chats are reachable by
  their visible chat-list name with an empty contact book (real-click
  row activation, composer verification). Typed drafts are
  whitespace-collapsed so a newline can never act as Enter from the
  non-sending open-chat tool, and typing re-fronts the window and
  strips control characters. comtypes NULL window pointers no longer
  raise.
- PowerShell output is forced to UTF-8 so Turkish titles survive.
- Chat auto-scroll goes through the scroller's own offset bookkeeping;
  replies no longer bounce the conversation to the top.
- Host voice-preference environment variables are scrubbed in the
  test suite so the suite stays hermetic. (An ElevenLabs adapter was
  built this session and fully removed the next: the user settled on
  a single built-in voice instead of a purchased one.)

## Voice identity, Turkish recognition, and real memory (23 August 2026, session 3)

- **Charon is the single voice of JARVIS.** After hearing samples
  the user settled on Charon; it is the default everywhere, the only
  registered cloud voice path, and the same multilingual voice speaks
  both Turkish and English. The stray `JARVIS_VOICE_GEMINI_TTS_VOICE`
  user-environment override was deleted so the setting's default is
  authoritative.
- **Recognition is pinned to Turkish** (`voice_language` defaults to
  `tr` with a firm transcription directive), ending wrong-language
  transcripts, and one transient transcription failure is retried
  before the turn can fail.
- **Voice and text now share one conversation.** Voice turns appear in
  the chat history, persist with the conversation store, and are
  restored on restart; asking in text about something said aloud
  works.
- **Long-term memory actually captures now.** Three layers: the
  explicit-prefix analyzer understands more Turkish ("unutma", "not
  al", "aklında tut") plus identity statements; the policy's
  preference path writes (it used to be dead code because the engine
  demanded an analyzer candidate); and an automatic post-turn model
  pass (`memory_auto_capture_enabled`, lite model) distills durable
  personal facts into third-person memories with paraphrase-aware
  deduplication, off the latency path, never able to fail a turn.
  Verified live against real Gemini: a casual sentence about the
  user's project became a stored memory.

## Implemented architecture

- `CoreEngine` provides bounded request, provider, conversation, memory, and
  tool orchestration.
- `ProviderGateway` provides capability-aware routing, explicit overrides,
  timeout, retry, health accounting, and normalized streaming; cross-provider
  fallback is intentionally disabled.
- `ConversationEngine` owns validated conversation lifecycle, complete tool-call
  groups, bounded context, and summaries.
- `ToolExecutor` owns strict input/output contracts, dynamic discovery,
  lifecycle, permission enforcement, timeout, cancellation, concurrency, and
  provider schema generation.
- `ExecutionService` owns plan-step execution, budgets, retries, verification,
  events, snapshots, recovery, and result propagation.
- `PermissionEngine` owns deterministic policy, parameter rules,
  least-privilege scopes, and bounded decision auditing.
- `ApprovalGate` and `ApprovalStore` own immutable, expiring, action-bound
  approval lifecycle. Final grant validation occurs at the tool boundary.
- `WindowsIntegrationService` owns trusted application registration, native
  system/process observation, safe process creation, and launch verification.
- `SQLiteMemoryStore` owns schema-versioned durable long-term memory,
  transactional persistence, integrity checks, and verified backup/restore.
- `MemoryManager` and `MemoryService` own safety screening, provenance,
  freshness, relevance, lifecycle, retention, and user-visible controls.
- `SQLiteTaskStore`, `TaskManager`, and `DurableTaskRuntime` own durable task
  identity, progress, subtasks, safe pause/resume, cancellation, and restart
  recovery through isolated plan and execution snapshots.
- `VoiceService` and `VoiceSession` own bounded microphone turns, explicit
  state, wake-word gating, interruption, speech provenance, and audio disposal.
- `VisionService`, `VisionConsentGate`, and `WindowsScreenSource` own one-use
  capture consent, native bounded capture, redaction, freshness, provenance,
  interruption, and vision-capability routing.
- `ResearchService`, `URLPolicy`, and `SafeWebFetcher` own opt-in search,
  IP-pinned safe retrieval, untrusted-content isolation, provenance, freshness,
  bounded multi-source synthesis, uncertainties, and citation integrity.
- `DesktopController` owns responsive background dispatch, live service state,
  and explicit user input while preserving all Core security boundaries; the
  Nova shell (`app/ui/nova`, pywebview/WebView2) is the default presentation
  and the classic Tk `DesktopWindow` remains behind `--classic`.
- `DiagnosticsService`, `DiagnosticLedger`, `MetricRegistry`, and
  `HealthRegistry` own sanitized structured events, tamper evidence, bounded
  low-cardinality metrics, and timeout-contained live health checks.
- `AdmissionController` and per-provider `CircuitBreaker` instances own bounded
  Core concurrency, queue deadlines, overload failure, dependency isolation,
  and single-probe recovery.
- The Windows release pipeline owns pinned PyInstaller analysis, bundled Tcl/Tk,
  logo resources, frozen smoke evidence, portable packaging, Inno Setup source,
  and artifact hashes.

## Completed phases

1. Phase 0 — workspace inspection and architecture foundation
2. Phase 1 — project bootstrap, configuration, and core contracts
3. Phase 2 — bounded JARVIS Core and execution runtime
4. Phase 3 — AI provider gateway and model routing
5. Phase 4 — conversation engine and context lifecycle
6. Phase 5 — versioned, dynamically discoverable tool system
7. Phase 6 — scoped permission policy and bound approval security
8. Phase 7 — native Windows observation and verified application launch
9. Phase 8 — durable, searchable, provenance-aware long-term memory
10. Phase 9 — durable, resumable, bounded task and agent execution
11. Phase 10 — bounded voice input, speech output, and interruption
12. Phase 11 — consent-bound vision and screen understanding
13. Phase 12 — source-grounded, SSRF-resistant web research
14. Phase 13 — native desktop UI from the approved visual prototype
15. Phase 14 — diagnostics, observability, health, and tamper-evident events
16. Phase 15 — complete-system acceptance and security regression gates
17. Phase 16 — bounded load, overload control, and provider circuit recovery
18. Phase 17 — reproducible Windows packaging, branding, and release evidence
19. Phase 18 — final acceptance audit and production-gap classification

## Current security decisions

- `READ_ONLY` and `LOW` operations are allowed by default.
- `MEDIUM` and `HIGH` operations require a valid bound approval grant.
- `CRITICAL` operations are denied by the default policy.
- Tool or plan metadata cannot lower effective risk.
- Parameter rules may elevate risk or force confirmation/denial, never allow.
- A raw confirmation boolean is not authorization.
- Approvals bind operation, tool version, parameters, task, plan, step, and
  expiry and are validated immediately before handler execution.
- Invalid filters, rules, scopes, grants, and approval parameters fail closed.
- Permission audit records do not retain tool parameter values.
- Windows launch accepts only registered local `.exe` definitions and verifies
  the returned PID and process identity before reporting success.
- Durable memory rejects credential, private-key, and payment-card material.
- Soft forgetting requires `MEDIUM`-risk approval; permanent deletion requires
  `HIGH`-risk approval and both are revalidated at the tool boundary.
- Task pause, resume, and cancellation require bound approval; interrupted work
  is paused on startup and never assumed complete.
- Voice is disabled by default, microphone capture is duration-bounded, and raw
  audio is overwritten and released after transcription unless retention is
  explicitly enabled.
- Vision is disabled by default. Every capture requires a short-lived, exact,
  one-use consent grant and configured privacy masks run before model access.
- Research is disabled by default. Every URL and redirect is DNS-validated,
  pinned to a public IP, content-bounded, and treated as untrusted evidence.
- Diagnostics redact secret-bearing fields and values, hash trace identity,
  bound event/metric growth, and never expose health-check exception details.
- Core work and queued callers are bounded; retryable provider outages open a
  circuit and later admit exactly one recovery probe.

## Known limitations and deferred work

- Window management, clipboard, notifications, and broader application-specific
  controls remain future Windows extensions.
- Gemini requires a configured API key. Without one the desktop reports a
  classified configuration error instead of answering.
- Filesystem search covers names only (no content index); plans work within
  one root; the compact window assumes the primary monitor.
- Native notifications are best effort (tray balloon or a PowerShell WinRT
  toast) and cannot deep-link back into the window; the tray's `Aç` does.
- Python cannot forcibly stop an already-running synchronous worker thread.
  Timeout results explicitly report when side effects may continue.
- The tray menu offers `Sesli mod`, which opens the voice screen and starts
  the same session as the page's microphone button.
- The tray icon exists for the Nova shell only; the classic Tk shell keeps
  the single-instance guard but has no icon. `Duraklat` does not stop
  reminders or the screen watcher.
- The plugin runtime is in-process (no operating-system sandbox) and has no
  settings-screen UI yet; plugins are enabled through `PluginRuntime.enable()`.
- Publisher code signing is not configured.
- Nova needs the Microsoft Edge WebView2 Runtime (shipped with Windows 11).
  When pywebview is missing or no runtime is detected, the classic shell opens
  with a Turkish notice in the chat and a `nova.unavailable` warning in the
  diagnostics ledger; nothing crashes silently.
- Voice from the Nova shell was exercised without speech input only; a spoken
  cloud (Charon) and local-fallback turn still needs the user's microphone.

## Verified Phase 7 vertical slice

Implement the first real Windows vertical slice through the existing security
and verification boundaries:

```text
User request
  -> provider tool call
  -> registered Windows tool
  -> permission/approval decision
  -> native Windows action
  -> independent state verification
  -> structured result
  -> natural response
```

The real Notepad launch path was executed locally, verified by its new PID and
process identity, then the test-created process was closed. Native system and
process observations were also executed successfully.

## Backup policy

After every completed phase, the verified project is mirrored to:

`C:\Users\MeGaComputers\Documents\Codex\JARVIS_BACKUPS\JARVIS`

Generated virtual environments, caches, transient logs, and temporary runtime
data are excluded because they are reproducible or non-authoritative.

## Verified Phase 8 vertical slice

The application persisted a memory to SQLite, closed the store, rebuilt the
application against the same database, recalled the memory with its source and
freshness metadata, created an integrity-checked backup, and restored that
backup into a separate database. Corrupt input failed closed. Concurrent
writes, expiry filtering, sensitive-data blocking, soft forgetting, and
approval-gated permanent deletion are covered by regression tests.

## Verified Phase 9 vertical slice

Completed. A real two-process validation persisted a running two-step task,
restarted the task manager, detected the interruption as paused, resumed from
the second step without repeating the verified first step, completed the task,
and restored its terminal state from an integrity-checked database backup.

## Verified Phase 10 vertical slice

Completed. A deterministic end-to-end voice turn captured bounded PCM audio,
converted it to WAV for transcription, applied an exact optional wake-word
gate, entered Core as a `VOICE` request, synthesized the response as WAV, and
sent it to the audio output. Tests verify interruption while listening,
processing, and speaking; timeout and failure classification; device closure;
audio disposal; provider limits; and single-session admission without network,
credentials, or physical audio hardware.

## Verified Phase 11 vertical slice

Completed. A deterministic screen frame passed through explicit one-use
consent, bounded capture, user-selected and automatic taskbar redaction, PNG
encoding, freshness verification, SHA-256 provenance, the real `CoreEngine`,
and capability-aware routing to a vision provider. The image entered Core as a
`VISION` request and was cleared after analysis. Tests cover consent tampering,
single use, stale images, invalid regions, timeouts, interruption, retention,
native-source boundaries, OpenAI image normalization, and conversation-history
privacy without network access or capture of the user's real desktop.

## Verified Phase 12 vertical slice

Completed. Deterministic SearXNG results pass through safe-result filtering,
bounded concurrent collection, source extraction, freshness classification,
cross-checking, synthesis, citation validation, and explicit uncertainties.
Tests verify public-IP pinning, redirect revalidation, IPv4/IPv6 SSRF defenses,
download and content limits, injection indicators, source hashes and timestamps,
strict tool contracts, and offline bootstrap behavior without network access.

## Verified Phase 13 vertical slice

The approved HTML prototype was translated into a native monochrome desktop
shell with eleven screens, collapsible navigation, live context, command
composer, two themes, and explicit voice/vision/research controls. Prototype
mock values were replaced by live application snapshots. Text commands pass
through the real Core; optional capabilities fail closed; one-capture vision
requires a user-visible confirmation immediately before consent creation.

The development harness has no Tcl data files, so this phase verifies the UI
through controller integration, complete state/render-module compilation, and
import-safe native presentation code. The packaged runtime smoke test remains a
mandatory Phase 17 acceptance check.

## Verified Phase 14 vertical slice

Core request start and completion events enter a sanitized, bounded,
hash-chained ledger with stable non-reversible correlation. Successful requests
update counters and duration summaries; provider failures record only a stable
error class. Concurrent live checks observe Core, provider registration, memory,
durable tasks, and event-ledger integrity under individual timeouts. Read-only
tools expose health, events, and metrics, and the desktop diagnostics view shows
the live ledger count and integrity result.

## Verified Phase 15 acceptance gate

The single-command verifier passed dependency integrity, complete bytecode
compilation, and all 795 tests under a fixed non-default hash seed. The offline
system acceptance path covered durable SQLite memory/tasks, Core, conversation,
UI state, diagnostics, metrics, health, and event integrity. All application
modules imported without external actions; every tool contract was unique,
closed-schema, versioned, JSON serializable, and risk-consistent. Static security
regressions confirmed no shell-enabled subprocess or dynamic `eval`/`exec` path.

## Verified Phase 16 load and recovery slice

Core admission limits active and queued work before any memory, provider, or
tool processing. Saturation, queue timeout, and cancellation release accounting
correctly and record content-free diagnostics. One hundred parallel mock Core
requests complete under an outer five-second budget without leaked leases.
Retryable provider failures open an independent circuit, suppress further remote
calls, admit only one half-open probe, and close or reopen from its verified
result. Provider circuit and admission state are included in live health checks.

## Phase 17 packaging evidence

The pinned Windows build produces a versioned, branded onedir executable and a
portable ZIP. The executable contains the approved monochrome JARVIS icon,
Windows version metadata, manifest, `_tkinter`, Tcl/Tk DLLs, complete Tcl/Tk
script data, voice binaries, and application documentation. The build pipeline
validates controlled cleanup, archive topology, static runtime completeness,
artifact hashes, signing status, and strict native smoke evidence.

All 817 source tests pass. The frozen process starts and reaches Tk creation,
but this sandbox's native Tcl file API reports `init.tcl` unavailable even while
Python and PowerShell read the same bundled file. The release manifest records
this as `environment_limited`, `ok=false`, and `native_ui_rendered=false`.
Inno Setup 7.0.2 was downloaded from its immutable official release and its
Pyrsys B.V. Authenticode signature verified, but its compiler installation is
blocked by the same sandbox profile-folder limitation. No production-readiness
claim is made until both gates pass on a normal Windows account.

## Phase 18 final validation

Completed. The single-command source verifier passes dependency integrity,
bytecode compilation, and all 817 deterministic tests. A dated runtime
dependency audit reports no known vulnerabilities. Static review found no
forbidden dynamic/shell execution path; abstract-provider `NotImplementedError`
methods and best-effort cleanup handlers are intentional boundaries rather than
unfinished product stubs.

The authoritative 28-item acceptance matrix is in `docs/FINAL_AUDIT.md`: 18
items pass, 9 are conditional on configured hardware/external services or a
normal Windows packaging host, and 1 is missing (the complete safe filesystem
tool family). JARVIS remains a development release until the recorded blockers
are resolved.

## Post-Phase 13 desktop and API configuration refresh

The native desktop was comprehensively redesigned around a restrained
black/white/grayscale system while preserving all eleven live runtime views.
The new shell includes scroll-safe content, clearer visual hierarchy, a
collapsible animated navigation rail, a reduced-motion control, a live status
pulse, improved conversation presentation, and keyboard-first operation.
`Enter` submits the composer, `Shift+Enter` inserts a line break, and additional
navigation, focus, theme, and help shortcuts are available through `F1`.

The Settings screen accepts the Gemini API key through a masked field, selects
the model, tests model access, saves non-secret preferences atomically, and
activates a rebuilt runtime without restarting the desktop. The secret is stored
only in Windows Credential Manager under `JARVIS/Gemini API`; it is never
written to the repository or preferences JSON. Mock echo responses are blocked
in the user interface and replaced by a clear configuration path. A live
provider failure can no longer fall back to the development-only mock provider,
so authentication, model, quota, and network errors remain visible.

## Gemini provider integration

Gemini is the sole production provider, reached through Google's
OpenAI-compatible API endpoint. Its credential lives in its own Windows
Credential Manager record. The default model is `gemini-3.5-flash-lite`, and an
optional `JARVIS_VISION_MODEL` routes `VISION` requests to a separate Gemini
model when the two differ.

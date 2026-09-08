# Medical Academy

A first-year medical-school study layer inside JARVIS: a tutor, a document
engine, a question engine, an evidence-based professor-style profiler, an
interpretable learning model and the Anatomy Lab. It is an extension of
JARVIS, not a second application — it reuses the same core engine, the same
permission and approval machinery, the same conversation store and the same
Nova shell.

## Scope

Seven first-year subjects, curated as data rather than code:

| Subject | Turkish label | Depth |
| --- | --- | --- |
| `anatomy` | Anatomi | Musculoskeletal system first, full Latin nomenclature |
| `histology` | Histoloji | Tissues, staining, identification |
| `microbiology` | Mikrobiyoloji | Bacterial structure, genetics, sterilisation, basic virology |
| `biochemistry` | Biyokimya | Molecules, enzymes, pathways, regulation |
| `biophysics` | Biyofizik | Transport, membranes, fluids, optics, radiation |
| `physiology` | Fizyoloji | Excitable tissue, systems, homeostasis |
| `biology` | Biyoloji | Cell and molecular biology, genetics |

## Package layout

```text
app/medical/
  data/curriculum.json    107 topics across the seven subjects
  data/anatomy.json       60 structures, 109 landmarks, 86 Latin terms
  data/concepts.json      ~200 learnable concepts and their relations
  data/prerequisites.json 63 curriculum-order prerequisite links, each with its reason
  models.py               every persisted record, plus serialization
  text.py                 Turkish/Latin folding, chunking, page ranges, similarity
  catalog.py              the curriculum, addressed by dotted topic ids
  terminology.py          alias-aware term recognition in free text
  concepts.py             the concept graph (relations answered both ways)
  store.py                SQLite persistence for everything the academy learns
  search.py               BM25 with synonym expansion
  retrieval.py            page-anchored evidence for grounded answers
  documents.py            PDF/text ingestion, page rendering, chunking
  schemas.py              JSON schemas for every structured model output
  model.py                one bounded, validated, repairing model call
  prompts.py              tutor and pipeline prompts
  questions.py            question quality, grading, similarity, exam analysis
  generation.py           question generation and exam assembly
  professor.py            exam import and evidence-based style profiling
  learning.py             mastery, spaced review, insights
  understanding.py        confidence-aware answer events, misconception findings, repair
  prerequisites.py        the prerequisite graph with provenance; prerequisite diagnosis
  review.py               source-support review, the student's flag, invalidation
  planner.py              the exam-date plan, curriculum coverage, the "Bugün" view
  histology.py            histology specimens cut from page figures; practicals
  study.py                the connected study workflow the bridge dispatches
  anatomy.py              the Anatomy Lab: structures, quizzes, 3D assets
  intents.py              deterministic medical intent parsing
  context.py              the persistent study session
  tutor.py                one parsed command → what the core should do
  academy.py              the facade the bridge, the tools and the engine use
```

## How a turn reaches the academy

`CoreEngine` consults one optional domain layer through
`app/core/augmentation.py`. On every **general** turn (identity, clock and
social turns are answered by Core itself and never handed over) the engine
calls the registered augmenter and applies what comes back:

- a **system prompt** replaces the interaction prompt for that turn;
- an **allowed-tools set** may only *narrow* what Core already exposed —
  a domain layer can never add a tool that was not already available;
- a **direct response** short-circuits the model entirely;
- **suppress_memory** stops the turn's material from being written to
  personal memory, and says so in the response metadata.

A failing, hanging or malformed augmenter is reported to the diagnostics
ledger and ignored; the turn completes without it.

The academy's augmenter is `MedicalAcademy.augment`. It parses the request
deterministically (about 0.5 ms — see *Latency* below), and only claims the
turn when the request is actually medical.

## Intent parsing

`MedicalIntentParser` turns natural requests into a `StudyCommand` with no
model call. It recognises the subject, the topic, anatomical structures and
landmarks, the study mode, and every stated constraint: question count,
option count, difficulty, page range, "cevapları en sonda", "tek tek sor",
"kopyalama", "sadece yanlış yaptıklarım", "hocanın tarzında".

Requests that are not medical (`hava nasıl`, `spotify'da müzik aç`,
`dosyaları listele`) return `MedicalIntent.NONE` and the academy does not
touch the turn. Study-shaped follow-ups ("5 şıklı olsun") are understood as
medical only when they carry a strong marker (a question count, a professor,
an exam word) or when study activity is recent — `contextual` mode, a
20-minute window.

## Documents

`DocumentPipeline` imports a PDF or a text file, deduplicating by SHA-256,
copies it below the academy directory, and reports real stages
(`Belge okunuyor` → `Sayfalar çıkarılıyor · 12 / 24` → `Kavramlar
dizinleniyor` → `Hazır`). No invented percentages: every number shown is one
the pipeline actually measured.

Text comes from pypdfium2 with headings detected per page. Pages whose
information is mostly visual (image area over the page, or almost no text)
are queued for the vision pass, bounded by
`JARVIS_MEDICAL_VISION_PAGES_PER_DOCUMENT`. Each such page is rendered to a
PNG and described by the vision model against `PAGE_VISUAL_SCHEMA`; the
description and its labels become a searchable chunk, so a figure a text
extractor could not read still answers questions.

Chunks keep their page number and character offsets, so **every citation
points at a chunk that exists**. `Retriever` never returns a page the store
does not hold, and a generated question keeps a source reference only when
the model's stated page matches a supplied one.

### Lecture sets and presentations

A whole semester arrives as a folder, so `import_folder` files everything
below it as one **lecture set**: the folder names become tags, the nearest
folder (or the file name) that names an academy subject sets the subject
(`folder_subject`: *Anatomi* → anatomy, *Tıbbi Biyoloji* → biology,
*Histoloji ve Embriyoloji* → histology, *Mikrobiyoloji* before *Biyoloji*
where one word contains the other), and the set record (`meta` table,
`lecture_set:<id>`) remembers what it took, what it skipped and why. The
counts a set shows in the library are computed from its documents every time
(`ready`, `pending`, `failed`, figure pages still waiting), never from what the
import hoped for. `process_lecture_set` walks the pending documents one after
another; each document reports its own stages, but its completion event is
marked `quiet` so the shell publishes one notification for the set instead of
two hundred. A failed document is retried only when asked.

The figure pass is paced for a free-tier provider: a short breath between
pages, a long wait after a refusal, and after two refusals in a row the pass
stops for that document (`looks_like_outage`: unavailable, rate-limited,
timed out, quota) with the remaining pages left *pending* — an outage is not
a verdict on a page, so nothing is marked failed by it — and the document's
status says so. `continue_processing` picks the work up later: for a set, a
document or the whole library it describes the pending figure pages and runs
the missing analyses one document after another, stopping at the first
outage. The set card's "Şekilleri incele" and the document's button of the
same name start it; the report says how many figures were described, how
many documents analysed, and why it stopped if it did.

Presentations (`.ppt`, `.pptx`) are accepted when PowerPoint is installed:
`OfficeConverter` exports the deck to PDF through PowerPoint's COM interface
from a PowerShell script (read-only, no window, alerts off) and caches the PDF
under `<medical directory>/converted/<sha256 of the deck>.pdf`, so the same
deck never converts twice. The document keeps its original file name and
records `source_format`; what is stored and read is the PDF. Without
PowerPoint the import says so (`JARVIS_MEDICAL_OFFICE_CONVERSION=false` says it
on purpose) and nothing is guessed. A file named `.pdf` whose bytes start with
the zip or OLE signature is treated as the deck it is.

Titles are the file's own name unless that name says nothing (`3.pptx`,
`Sunu1`): only then the PDF's Title field is consulted — and a Title that is a
person or a tool default is not a title — and after that the first heading of
the opening pages, skipping lines that name a lecturer. An empty text file is
refused instead of becoming a "ready" document with nothing in it.

### Comparing lecture material with standard knowledge

`compare_document` classifies substantive statements as consistent,
simplified, incomplete, potentially misleading, possibly incorrect, or a
terminology difference — each with the page it came from, an explanation,
what standard references say, and a support level in words. A page the model
states but the evidence does not contain is shown as *sayfa doğrulanamadı*
rather than being trusted.

## Questions and exams

Generation is grounded (lecture evidence + curated facts + concept hints +
the professor directive) and then filtered by deterministic code:

- `validate_question` rejects short stems, wrong option counts, non-sequential
  keys, duplicate options, "hepsi/hiçbiri" options in their inflected forms, an
  option that names another option by letter ("A ve B doğrudur" — shuffling
  re-letters the options, which would turn it into a false statement), a missing
  or impossible answer key, an obviously longest correct option, a stem that
  contains its own answer, a missing explanation and an out-of-range difficulty.
  An imported professor question is exempt from the letter rule: it is stored as
  it was written and never re-lettered;
- `is_too_similar` rejects a near-copy of any existing question — including a
  reworded professor question with the same answer;
- `shuffle_options` re-letters seeded, so answers do not cluster on A.

What was rejected is reported in the exam's generation notes, never hidden.

A generated question cites the excerpt it used by the `[Kaynak N]` index the
prompt printed, not by its page number: two documents routinely share a page, and
resolving by page alone attaches a real title and page to material the question
never used. Index and page are two claims about the same excerpt and must agree;
when they do not, or when the index names an excerpt that was never sent, the
question is stored with no citation and its origin falls back to *üretilmiş*. A
missing citation is honest where a chip that opens the wrong page is not.

**Which pages are figures.** The vision pass reads a bounded number of pages
per document (`JARVIS_MEDICAL_VISION_PAGES_PER_DOCUMENT`): pictured pages first
— an embedded image covering enough of the page, or an image on a text-poor page
— then drawn diagrams, which a slide exported from a drawing tool carries as
paths rather than pixels (labelled bone outlines, pathway arrows): enough path
objects with little text. A text page with a few rules is not a figure. The
same rule decides which pages "görselli sorular" may draw on.

**Figure questions.** With "görselli sorular" on (the exam form's default, and
always on for a chat-started paper), the generator offers the model the lecture
pages the vision pass has described — the description and its labels are the
only facts a figure question may ask about — and a question that names one of
those figures is anchored to that page: the runner and the results show the
rendered page beside the stem, captioned with the document and page. JARVIS
never draws anatomy of its own: a figure is always one of the student's pages,
and a figure index the model invents yields a question with no figure.

**The paper, not the chat.** A typed "beni sına" builds a short paper with the
answers at the end and opens it on the exam screen (`exam_ready` with `open`):
options to mark, a finish button, and then the results with the wrong answers
first — each with the correct option and its explanation — the blanks, the
correct ones, and the breakdown by topic, difficulty and subject. The
letter-by-letter chat quiz remains for the voice loop, where there is no screen
to mark on. When nothing was answered wrongly the suggestion names the blanks
rather than a "weakest topic" no wrong answer supports.

**Over the wire.** Gemini's OpenAI-compatible endpoint refuses a JSON schema
carrying nested `minItems`/`maxItems` with 400 INVALID_ARGUMENT (measured live),
so `MedicalModelClient` sends `wire_schema()` — structure, types, enums and
required only — and enforces every bound locally through `validate` and
`coerce_strings`; the full schema is still printed in the prompt.

A "sadece yanlış yaptıklarım" paper is built from the missed questions and
nothing else (`from_bank(..., only_wrong=True)`): if fewer were missed than
were asked for, the paper is shorter and the note states the number found.
Padding it from the bank would label questions the student never got wrong as
their own mistakes.

Scoring, breakdowns (subject, topic, difficulty), weak and strong concepts,
the review list and the next-step suggestion are all computed in
`analyse_attempt` — deterministic, explainable, no model involved.

Finishing is done once per attempt. `finish_exam` runs under the academy
lock and in one store transaction: a simulation's answers are recorded into
mastery, the analysis (with the adaptive verdict — previous difficulty,
suggested difficulty, reason) is stored with the attempt, the session's
difficulty moves and the exam is marked completed, together or not at all.
A second finish for the same attempt — a repeated click, a retried call —
returns the stored result exactly as it was and emits no second completion;
starting the exam again opens a new attempt that is finalized on its own.

## Professor style

Imported questions are parsed deterministically: numbered stems, lettered
options on their own lines or inline, an inline `Cevap: C`, or an answer table,
which states the keys of the questions above it — a file holding two papers keeps
both, and a table with no header ends where its own key lines end rather than
consuming a fixed block of the paper. **An answer key is never guessed.** A
question whose key the text does not state is stored without one and shown as
*cevap anahtarı yok*; the user can mark it themselves. A sentence that only
mentions a letter ("Cevap D değildir") is not a key, and the stem keeps it.

When the deterministic parse is short and a provider is configured, the model
is asked to read the paper as well — but its reading is stored only when it
recovers more questions than the parser did, and the import note says which of
the two the stored questions came from.

`StyleProfiler` measures fifteen observable features (negative stems, "which
is true", clinical vignettes, multi-statement items, Latin density, numeric
facts, definitions, structure recognition, mechanism, exceptions, stem
length, distractor similarity, option count) and reports each as
`observed / total` with a level. Confidence follows the sample size only:
under 10 questions it is *sınırlı* and the profile says so in plain Turkish.
The generation directive repeats **only ratios that were actually observed**.

### Professors from the material

Lecturers write their name on the title slide, and sometimes into the file
name (`Mikrobiyoloji Ülker Çuhacı 2 - Mantarlar`). `mine_questions` reads
both: `professor_mentions` looks at the opening pages (and, failing those,
the closing ones) for a line that *starts with an academic rank* in any of its
spellings (`Prof. Dr.`, `PROF.DR.`, `Yrd.Doç.Dr.`, `Doktor Öğretim Üyesi`,
`Öğr. Gör.`, `Uzm. Dr.`) followed by one to four name tokens; a bare
abbreviation that opens an ordinary sentence (`Arş. Geliştirme…`, `Öğr.
Elemanları…`) is not a rank, a job after the rank (`Dr. Halk Sağlığı Uzmanı`)
is not a person, and a name cut by a line break (`Dr. Hasan` / `OZAN`) is
joined. Spellings of the same person are merged by `same_person`: titles
dropped, letters folded, an initial agreeing with the first name, and a
surname the text layer broke apart (`Kürkçüo lu ğ`) matched by letter
similarity. One profile per lecturer; the fuller spelling wins.

Each lecture is stamped with its lecturer (`document.professor_id`), and the
numbered, lettered questions inside it — the review questions many slide
decks end with — are filed under that lecturer with the `lecture_derived`
origin, the page they were found on, and the page as their figure when it
carries a picture. Keys are never guessed. A lecture is never cut at a name
mentioned inside it (`Dr. Refik Saydam` in a history lecture is content); only
a file that *looks like a compiled paper* (`looks_like_question_paper`: at
least five questions and most lines question-shaped) is split at the headings
that name a lecturer, each section going to the heading above it.

An exam system's export names the author of every question — `Soru Sahibi :`
under the stem, `Anabilimdalı :` for the department, `KOMITE N` at the top of
the page, the key as a suffix on the option itself (`-Doğru Seçenek`) and the
student's own mark as another (`-Öğrencinin işaretlediği`), which is never
read as a key. The parser reads all of it: each question goes to its own
owner (merged with a lecturer already known from a lecture by the same
surname rule), keeps its committee, department and the student's mark as
metadata, takes its subject from the department, and carries the key the
paper marked; two options marked correct name no key. Such a paper belongs to
nobody as a whole. The style profiles built this way rest on keyed exam
questions and their note says so.

The real scans taught the parser three more things. A key suffix the scan
wrapped onto the line below the option (`D) … olur.` / `-Doğru Seçenek`)
still marks that option: a continuation line is re-read for the suffixes. A
page that prints the options beside a figure, *above* the owner lines, has
no options after them, so the option-shaped lines held back as stem become
the options after all (the `A. subclavia …` rule only holds while real
options follow the owner lines). And a question whose owner lines are
missing from the print keeps its options and its key; it is filed under
nobody rather than under the previous owner. Two questions are the same
question only when stem *and* options agree (`question_fingerprint`):
"Aşağıdaki ifadelerden hangisi yanlıştır?" opens a dozen questions per
semester, and each is kept.

The papers Ali shared are scans with no text layer, so a document whose pages
hold no text is first *transcribed*: `transcribe_document` renders each page
and asks the vision model to write it out exactly as printed (headings,
numbers, owner and department lines, options with their suffixes, nothing
added or corrected), stores the transcription as the page's text, re-indexes
the chunks and tags the document *taranmış metin*; questions filed from it
carry `ocr: true`. The pass has the figure pass's pacing and outage rule, runs
first inside `continue_processing` ("Metne çevir (OCR)" on the document,
"Şekilleri incele" on the set), and a page it could not read stays as it
was.

A published book is not a lecture: front matter with an ISBN (or a publisher's
mark meeting an editorial one) marks the document as a book, its end-of-unit
questions are filed under nobody and tagged *kitaptan*, and its editors and
authors never become the student's lecturers. A deck whose title slide is a
picture carries its lecturer only in what the vision pass wrote about that
page; that description is read too, but only for the first pages, only when
the page has no text of its own, and only when the description says it is a
cover — a portrait inside a history lecture names a person as well, and that
person is not the lecturer.

The report says what happened: lecturers found with their lecture counts,
questions filed, books recognised as books, lecturers read from a pictured
cover, lectures with no name anywhere, names read incompletely, and
that the profiles built from review questions are limited until a real exam
paper is uploaded. "Bu tarzda sınav" for a lecturer whose lectures are in the
library draws its evidence from those lectures (up to twelve) and takes the
lecturer's subject when none was chosen.

## Learning model

`LearningEngine` is deliberately interpretable:

| Level | Rule |
| --- | --- |
| `unknown` | fewer than 2 attempts |
| `weak` | recent accuracy below 50% |
| `moderate` | recent accuracy below 80%, or fewer than 3 attempts |
| `strong` | recent accuracy 80% or better |

The recent window is the last 8 attempts. Review intervals follow the level
(1, 1, 3, 7 days, growing with the streak up to 30). Every queued review
says why it was queued. Adaptive difficulty needs five recent results and
moves at most one step, and it says what it did; the verdict is part of the
attempt's stored result and is never recomputed from a later difficulty.

Wrong-answer choices are counted per concept, so an insight can name the
actual confusion ("Scapula sorularında 2 kez fossa supraspinata seçeneğine
kaydın") instead of a generic encouragement. The wording follows the count: one
observation is reported as one, not as a tendency, and two distractors picked
equally often are both named rather than one of them chosen arbitrarily.

## Sesli anlatım

A lecture can be read aloud, and the student can stop it anywhere to ask what
they did not understand. `app/medical/narration.py` has three parts.

**The script.** `NarrationBuilder` turns the document's pages into spoken
segments: the model receives up to six pages at a time and writes 70–170-word
segments in natural spoken Turkish (Latin terms said in full, no lists or
markdown) that say what the pages say, each with a title and the pages it
covers — a page the batch did not contain is clamped to the batch. Without a
model, or for a batch the model could not narrate, the pages are read as they
stand (`material_segments`) and the script's notes say so. Scripts are cached
in the store (`narration:<document id>`) and forgotten with the document.

**The player.** `NarrationPlayer` speaks a segment in sentence groups of about
two hundred characters, so a command lands within a sentence or two: pause,
resume, next, previous, stop, a typed question, a microphone question, and
the checkpoint switch. Commands come from the bridge thread and are applied on
the player's loop, which reads them between chunks — the state machine is
single-threaded. A question pauses the reading, is answered by the model from
the segment being narrated and the pages around it (four spoken sentences at
most; without a model the player says so aloud), the answer is spoken, and
the reading resumes at the same chunk. With checkpoints on and a microphone
available, every N segments (`JARVIS_MEDICAL_NARRATION_CHECKPOINT_EVERY`)
JARVIS asks *"Buraya kadar sorun var mı?"* and listens for a few seconds;
silence or a *yok / devam* continues, anything else is answered. Every state
change is pushed to the page as `narration_state`, and the last dozen
questions and answers travel with it.

**The voice.** `NarrationSpeaker` uses the local Windows voice by default
(`JARVIS_MEDICAL_NARRATION_VOICE=local`): it has no daily quota and never
changes voice in the middle of a lecture. With `cloud` the Gemini voice reads
until it fails or its quota runs out, and the rest of the lecture continues
locally with a note that says why. Narration and the voice session share one
microphone and one speaker, so each refuses to start while the other is
active. The page shows the narration panel above the academy tabs wherever
the student is: title, segment and pages, the text being read, the last
answers, a question box, and the controls (Space pauses and resumes).

## Anatomy Lab

The lab shows a curated structure card (Latin name, parts, surfaces,
borders, articulations, muscle and ligament attachments, landmarks,
high-yield facts; for muscles origo/insertio/innervatio/functio; for joints
type, surfaces, capsule, ligaments, movements with plane and axis), the
relationship map, movement data and a deterministic quiz. The quiz has two
sources: landmarks to identify on a bone, and the curated tables — a region
card such as the cranial-nerve overview or the skull base has no landmarks,
but every table row is a fact, so it asks a nerve's exit or a foramen's bone
with the other rows' values as distractors (short list-like cells only; a
sentence-long cell is left out). A quiz distractor is checked against the
structure it is asked about: an option that is
also a true statement for that structure and that fact is never offered, so the
student cannot be marked wrong for a correct answer.

**Geometry is never invented.** A 3D mesh is rendered only when a licensed
asset is registered in `anatomy_assets/manifest.json` next to the academy
data. Landmark labels are placed through the same transform as the mesh itself,
so a label cannot drift onto another part of the bone, and an anchor that falls
outside the model's own bounds is drawn nowhere rather than approximated:

```json
{
  "assets": [
    {
      "structure_id": "scapula",
      "file": "scapula.obj",
      "license": "CC BY 4.0",
      "source": "…",
      "attribution": "…",
      "side": "right",
      "up_axis": "z",
      "landmarks": {
        "acromion": [0.31, 0.62, 0.04],
        "angulus_inferior": {
          "anchor": [-121.3, -80.2, 1090.1],
          "confidence": "approximate",
          "method": "geometric extreme of the mesh: z min 0.03"
        }
      }
    }
  ],
  "scenes": [
    {
      "scene_id": "upper_limb_right",
      "title": "Üst ekstremite (sağ)",
      "region": "upper_limb",
      "structure_ids": ["clavicula", "scapula", "humerus", "…"]
    }
  ]
}
```

An entry without a licence and a source is refused. Without an asset the lab
says so and draws the schematic relationship map instead — which is labelled
schematic, not anatomy.

When a mesh is present the page renders it in WebGL on a black stage, in
matte tissue colours that follow the atlas convention — bone ivory, cartilage
cyan, muscle red-brown, arteries red, veins blue, nerves yellow — with every
pair held apart in RGB by a page test. The shading has no specular term and
no rim light, so nothing glares; detail comes from a low ambient floor and a
grazing-angle darkening, so the grooves between muscle bellies and the lip
of a fossa draw themselves as light against dark.

### Where the meshes come from

The meshes are BodyParts3D (The Database Center for Life Science), imported
by `scripts/import_bodyparts3d.py` from the polygon-reduced archive
`isa_BP3D_4.0_obj_99.zip`. The student downloads the archive; JARVIS fetches
nothing, and no mesh is committed to the repository:

```
python scripts/import_bodyparts3d.py isa_BP3D_4.0_obj_99.zip
```

The importer maps 25 structures of the right upper limb — bones, muscles,
arteries and veins — from their FMA identifiers to the archive's element
files, merges the element files of one structure into one OBJ under the state
directory, and writes a manifest entry per structure with the licence, the
attribution, the side, the up axis and the provenance (FMA ids, element files,
archive). The structures form the scene `upper_limb_right`. The licence is
recorded as the dataset states it — the archive page says CC BY 4.0, the OBJ
headers cite CC BY-SA 2.1 Japan — and the lab shows the attribution under the
viewport. Every mesh has a curated card: six vessel cards (arteria axillaris,
brachialis, radialis, ulnaris; vena cephalica, basilica) were added with
origin, course, branches or tributaries, territory and relations.

Two things the dataset does not have are not made up. BodyParts3D 4.0 ships
no bony landmarks as parts, and no peripheral nerves of the arm. Nerves keep
the schematic map. Landmark pins are *derived* from the shape where a rule can
place one — the most proximal cap of the humerus is its head, its most distal
medial corner the medial epicondyle. A rule (`LANDMARK_RULES` in the importer)
is a chain of cuts along the body axes and the pin is the centroid of what
survives. Every pin written this way carries `"confidence": "approximate"` and
its method, the lab draws it with "≈", and a landmark no rule can place (a
groove, a crest) gets no pin at all. A pin placed by hand outranks a derived
one on re-import.

### The scene

The lab opens the scene rather than one bone: every mesh of the scene in the
one body frame the dataset uses, coloured by kind, with a layer chip per kind
(kemik, kas, arter, ven) to hide and show. Clicking a mesh selects that
structure — the picking pass draws each mesh in a flat identifier colour and
reads the pixel under the cursor — and the structure list follows. Dragging
turns the model about the viewer's own axes: a pull to the right spins the
near face to the right, a pull down tips it down, from whatever orientation
it already has, with no clamp, so a bone can be looked at from below or
upside down like one held in the hand (the rotation is accumulated as a
matrix and re-orthonormalised after every step). The right button or
Shift+drag pans along the screen's axes; the wheel zooms; the arrow keys pan,
Shift+arrow turns, `+`/`−` zoom and `R` resets; the pad in the corner does the
same for those who do not know the gestures. Pan scales with the distance so
a step moves the picture by the same amount at any zoom.

One defect is worth recording because it hid for a whole day: the schematic
relationship map is an SVG, and an SVG element has no `hidden` property, so
`schematic.hidden = true` created an expando and hid nothing. The transparent
map lay over the canvas and swallowed every drag, click and wheel — the model
never turned under the mouse, and only the pad and the keys worked. The lab
now hides it through the attribute (`setHidden`), which is what the CSS rule
reads, and a page test forbids the property form.

A scene may carry a **palette**, a colour per structure, for a region whose
kinds would not tell its parts apart: the skull is all bone. Such a scene is
drawn and hidden structure by structure — one chip per bone in its own
colour, so the vault can be taken off to see the base — and opens on its
**card** (the region's explanation) rather than on a mesh.

**Detailed mode** (Detaylı) draws every pin of the selected structure with its
Latin name on the model. Clicking a pin opens a card with the landmark's
curated description and the pages of the student's own library that mention
it, each opening in the reader. The card searches the library; it does not
write what a landmark is from memory.

### Neurocranium

The braincase is the region first-year students find hardest, so it has a
card of its own, `neurocranium`, written as a study guide rather than a
list: what the calvaria and the base are, the sutures and craniometric points
(bregma, lambda, pterion, asterion), the articulations, the high-yield
clinical anatomy (pterion and the middle meningeal artery, the fracture
signs of each fossa, the herniation at the foramen magnum), and *how to
study it*. Two curated tables carry the part that has to be memorised: the
three cranial fossae with their bones, boundaries, contents and foramina, and
a foramen-by-foramen table of what passes through (the cranial nerves by
number, the vessels, the emissary veins). Six bone cards — os frontale, os
parietale, os temporale, os occipitale, os sphenoidale, os ethmoidale — carry
the documented bone fields plus eight to fifteen landmarks each, a landmark's
note naming the fossa it belongs to. Paired bones are one card each; the
facts are written once. Everything is curated from the standard texts (the
data file names them); nothing on these cards is generated.

Tables live in a structure's facts as `{"title", "columns", "rows"}` and
reach the page in exactly that shape (`AnatomyLab.tables` drops a table
without columns or rows, and pads or trims a row to the column count). The
curriculum gained the topic `anatomy.musculoskeletal.skull` with
`neurocranium` and `viscerocranium` beneath it, so the card's breadcrumb,
the tutor's topic resolution and the mastery record all have a real node.

The importer's second scene, `neurocranium`, maps the eight bones (the
paired ones merged from both sides' element files) to their BodyParts3D
files, colours them by the atlas convention, opens on the region card, and
derives approximate pins from the shape where a rule can place one — the
foramen magnum's pin is the centroid of the lowest rim, the crista galli's
the top of the midline, the mastoid process the lowest posterior point of
the right side. A foramen or a meatus is a hole with no surface of its own:
no rule, no pin.

```
python scripts/import_bodyparts3d.py isa_BP3D_4.0_obj_99.zip --scene neurocranium
```

### Viscerocranium

The facial skeleton completes the skull. An overview card (`viscerocranium`)
carries the two tables first-years are always asked for — the bones of each
orbital wall, and which meatus every paranasal sinus drains into (the
maxillary sinus and all but the sphenoid to the middle meatus, the sphenoid to
the sphenoethmoidal recess, the nasolacrimal duct to the inferior meatus) —
with a study guide for the orbit, the nasal wall and the pterygopalatine
fossa. Eight bone cards follow: maxilla and mandible in full detail (their
processes, the infraorbital and mental foramina and the inferior alveolar
canal, the temporomandibular joint), and os zygomaticum, os nasale, os
lacrimale, os palatinum, concha nasalis inferior and vomer. Paired bones are
one card each.

The importer gained a `cranium` scene that draws all fourteen skull bones —
the six braincase cards plus the eight facial bones — coloured one per bone,
opening on the neurocranium card, so the whole skull can be turned and taken
apart layer by layer:

```
python scripts/import_bodyparts3d.py isa_BP3D_4.0_obj_99.zip --scene cranium
```

### Vertebral column

The axial skeleton's backbone. An overview card (`columna_vertebralis`)
carries two tables — the five regions with the single feature that tells each
apart (the cervical transverse foramen, the thoracic costal facet, the large
bodyless lumbar) and the four curvatures with which are primary and which
secondary — plus a study guide. Cards follow for the two atypical vertebrae
(the bodyless atlas and the dens-bearing axis) and the typical cervical,
thoracic and lumbar vertebra, and the coccyx; the sacrum card already shipped.
The importer's `vertebral_column` scene draws a continuous spine by merging
each region's whole run of vertebrae into one coloured group (the twelve
thoracic vertebrae are one mesh), so the column is complete without a card for
every single bone, and the regions can be hidden one at a time to see the
curvatures. Atlas, axis and sacrum carry derived pins; the merged groups do
not, since a pin would fall in the empty centre of a stack.

### Craniovertebral and jaw joints

Three joint cards tie the skull to the jaw and the spine: the temporomandibular
joint (articular disc, gliding and hinging compartments, the muscles that open
and close it, anterior dislocation) and the atlanto-occipital and atlanto-axial
joints — the 'yes' of nodding at the occipital condyles and the 'no' of rotating
around the dens, held by the transverse ligament of the atlas. Each is a curated
joint card with the documented joint fields and is quizzed on its type.

### Thoracic cage

The rib cage completes the trunk. An overview card (cavea_thoracis) sorts the
ribs into true (1-7), false (8-10) and floating (11-12) and lays out a typical
rib's parts and the costal groove that hides the neurovascular bundle, with
cards for the typical rib, the atypical first rib (scalene tubercle, subclavian
grooves) and the floating ribs; the sternum card already shipped. The
importer's thoracic_cage scene draws the ribs, the three-part sternum and the
thoracic spine together as a rib cage.

### Abdominal wall

The anterolateral abdominal wall completes the trunk. An overview card carries
the rectus sheath (above and below the arcuate line) and the inguinal canal
(its four walls, the deep and superficial rings, and the spermatic cord or
round ligament) as tables, with the Hesselbach triangle and the hernia
distinction. Cards were added for the three flat muscles (external and internal
oblique, transversus abdominis) and rectus abdominis, each with the documented
muscle fields. The sheets stay schematic (no 3D).

### Head and neck vessels

The carotid system completes the head and neck. An overview card (vasa_colli)
carries the eight branches of the external carotid and the contents of the
carotid sheath as tables, with the key rule that the internal carotid gives no
branch in the neck and the carotid sinus and body sit at the bifurcation. Cards
were added for the common, internal and external carotid arteries, the
vertebral artery and the internal jugular vein. A new curriculum branch,
anatomy.cardiovascular.head_neck_vessels, holds them; the cards are schematic
(no 3D).

### Lower-limb vessels

The lower limb gains its vasculature to mirror the upper limb. An overview card
(vasa_membri_inferioris) carries the arterial line (external iliac → femoral →
popliteal → tibial) with each name change at its landmark and the
femoral-triangle contents (NAVEL), with the pulse points and the great
saphenous vein's landmark at the medial malleolus. Cards were added for the
femoral, popliteal and anterior and posterior tibial arteries and the femoral
and great saphenous veins.

### Cranial nerves

The twelve cranial nerves have a section of their own, tied to the skull
foramina the neurocranium work added. An overview card (`cranial_nerves`)
carries a high-yield table — nerve, number, type, skull exit, main function,
lesion sign — and a study guide (how to group them by type, by brain-stem
level, and by the reflexes they share). Twelve nerve cards follow, each with
its functional components, nuclei, exit foramen, course, motor and sensory
fields, parasympathetic branch where there is one, how it is tested at the
bedside, and its lesion signs. The exit named on each card is one the skull
base's own foramen table lists, so the two cards never disagree; a test holds
them to that. The cranial-nerve fields (number, components, nuclei, exit,
parasympathetic, examination) are optional on a nerve card, so the limb nerve
cards are unchanged — they still show only origin, course, motor, sensory and
high-yield. The curriculum gained `anatomy.neuroanatomy.cranial_nerves`.
Cranial nerves have no licensed mesh, so their cards are schematic; nothing on
them is generated.

### Bell-ringer (zilli sınav)

A bell-ringer (spotter) is the practical anatomy exam: numbered pins on
specimens, one station each, a fixed time per station, a bell that moves
everyone on, no going back. The lab keeps those rules. Up to ten stations are
drawn at random from the pinned landmarks of the scene (or of the open
structure); each station shows only the numbered pin, and the landmark list of
the card is hidden while the exam runs so the answer sheet is not on screen.
The student types the Latin name; the timer (30, 45, 60 or 90 s) rings a bell
and moves on. Matching is lenient the way an examiner is: case, Turkish
letters, diacritics, punctuation and the `m.`/`n.`/`a.`/`v.` prefixes are
ignored, and a token of five letters or more is accepted as the start of the
word it abbreviates. At the end every station is listed with the student's
answer, the correct name and its description, and each station was recorded
as an anatomy answer, so the structure's mastery moves with it.

One answer per station. The station is claimed the moment an answer is
given — before the save is awaited — so a second click, a second Enter or
the bell landing on a manual answer does nothing, and the exam advances
once, after the academy accepted the save. A station is graded against the
pin fixed when it opened, whatever the student selects in the list
meanwhile, and cannot be answered while its specimen is still loading. A
reply that arrives after "Bitir" or "Yeniden" belongs to the exam it was
sent from and changes nothing in the one that replaced it. A save the
academy refused or the bridge failed is said so ("Cevap kaydedilemedi: …")
with two ways on: "Tekrar dene" resends the same submission id, which
`record_anatomy_answer` recognises so a lost reply cannot count a station
twice, and "Kaydetmeden geç" moves on with the station marked unsaved in the
results.

## The connected study workflow

`StudyWorkflow` (`app/medical/study.py`) composes five services over the same
store, learning model and curriculum, and names every operation the page can
ask for: `SYNC_ACTIONS` answer from the store, `ASYNC_ACTIONS` need the model
and run as bridge jobs that report back with a `job_report` push (`job:
"study"`, dispatched by `action`), and `CONFIRMED_ACTIONS` (`invalidate_question`,
`plan_delete`, `histology_delete`) refuse a call without `confirmed: true`.
The Nova page adds three tabs — **Plan**, **Anlama**, **Histoloji** — two
dashboard cards (**Bugün**, **Anlama**), and touches the exam runner, the
results, the question bank and the library page (`js/study.js`, `css/study.css`).

### Confidence and reasoning with every answer

An answer may carry `confidence` — `sure`, `unsure` or `guess` ("Eminim /
Kararsızım / Tahmin ettim", chips above the options) — and a `submission_id`,
so a repeated click or a retried call records it once. `UnderstandingEngine`
stores one *event* per answered question — identified by the attempt and the
question, so a practice answer and the finish of the same paper record it
once — with the key, the confidence, the reasoning, where it came from (exam,
check, diagnosis, repair, histology) and a rule-based classification (`correct_supported`, `correct_unsupported`,
`correct_contradictory`, `wrong_low_confidence`, `wrong_high_confidence`).

Reasoning is asked sparingly. In a paper with immediate feedback the runner
asks "Kısaca neden?" after the answer for at most two questions per exam,
chosen by a stable hash (a quarter of the questions qualify), and always when
the concept already has an open finding. A paper marked at the end records its
events once when it is finished, and the results offer a reasoning box for the
first five wrong answers. Reasoning is judged by one bounded model call
(`REASONING_ASSESSMENT_SCHEMA`, one attempt): supports, contradicts, unclear,
with the suspected misconception quoted from the student's own words. The
verdict names its assessor; without a model the reasoning is kept and marked
unassessed. Neither the reasoning nor its verdict changes the exam mark.

### Misconception findings and their repair

A *finding* is a statement about one concept ("Yükselen fazı K+ girişi
sanıyor") with the evidence behind it. A question that names its concepts is
evidence about them. An imported committee question names none, so it is read
against the concept graph — its stem, its correct option and its own
explanation, never a distractor, which would file the finding under the very
structure the question ruled out. When nothing matches, the finding is
anchored to that question rather than to a topic or a whole subject: two
unrelated questions never feed one finding, and the repair looks the question
up instead of a subject name. Coverage still sees these findings, by topic. One piece of evidence opens a
*hypothesis*; two distinct pieces (a plain low-confidence wrong answer counts
0.7, a confident wrong answer or contradictory reasoning 1.0, a diagnostic
answer 2) make it *supported*. The student can challenge (`disputed`), dismiss
or reopen it, each with a note in the finding's history. A short open-ended
diagnostic question can be asked (model call; its expected answer and rubric
stay in the record and are never rendered) and the answer confirms the finding,
leaves it, or — twice refuted — withdraws it.

A *repair session* has five steps: the problem in words, the lecture passage
retrieved for the concept with its page chips, a short explanation (model, or a
pointer to the passage when there is none — labelled; the prompt carries the
question, the chosen option and the key, so the model cannot name the correct
answer as the mistake), a *transfer* question generated in a different context
with its similarity to the original stated (the directive carries the point the
original question tested),
and a delayed follow-up: the concept is scheduled in the review queue three
days later. Answering the transfer question right marks the finding
`repair_demonstrated`; only a correct answer on that concept at least two days
later, in an exam, a check or a histology practical, *resolves* it
(`confirm_follow_ups`, run when a paper is finished). Invalidating a question
withdraws the evidence that rested on it, and a finding left without valid
evidence is withdrawn too.

### Prerequisites with provenance, and the prerequisite diagnosis

`PrerequisiteGraph` holds *concept requires concept* edges. Sixty-three ship in
`data/prerequisites.json` as reviewed curriculum-order links, each with the
reason it matters. New edges arrive as suggestions — imported from material,
proposed by the model, or named by the student in the Anlama screen (a name box
backed by `concept_search`) — and stay *pending* until the student confirms
them; a confirmation that would close a cycle is refused with the path that
causes it. Every edge shows where it came from. Lookups walk two levels and use
reviewed edges only.

When a concept keeps failing (a supported finding, or a weak mastery row with
three attempts or more) the Anlama screen offers "Ön koşulu teşhis et". The
diagnosis says what it is doing ("X konusuna dönmeden önce şu temel kavramı
kontrol edelim"), takes at most three nearest, weakest-known prerequisites,
asks at most three answer-keyed bank questions (skippable; "Kısalt" jumps to
the nearest), and locates the foundation: a path back to the objective with
one activity per step, a labelled estimate and "Orijinal hedefe dön" at the
end. A concept with no recorded prerequisites, or prerequisites without bank
questions, says so and lists what the student can do instead.

### The exam-date plan and "Bugün"

A plan names an exam and its date (a date, in the student's own zone), the
scope (subjects, topics, documents, page ranges — inferred from the library
when none is given, and then shown as a proposal that must be confirmed
before any planning), the minutes available per weekday, the days that are
not, optional weights and priorities, and a morning reminder delivered
through the reminders service. Coverage classifies every topic in scope:
*unstudied*, *studied_unassessed* (a page was opened — reading logs count as
studied, never as demonstrated), *assessed_limited*, *demonstrated* (strong
mastery, reasoning that held up, no open finding), *due_review*,
*misconception* (an open supported finding). From those states the planner
lays out one day at a time within the day's budget and never above it, up to
fourteen days ahead, with the reason and an estimated duration on every
activity. Estimates start from a default per kind and blend in the minutes
activities actually took; an activity longer than the day is split and says
so. Planned, started, skipped, completed and missed are five different
states; nothing completes on a timer. When the scope cannot fit the remaining
budget, the plan says so with the numbers and lists what stays uncovered.
Replanning — a new day, a changed budget, a day off, a changed scope, or on
request — keeps completed and manual activities.

"Bugün" (a dashboard card and the Plan screen) shows the next activity with
its estimate labelled as one, and Başla / Bitti / Atla; starting a repair
opens the finding, starting a prerequisite check opens the diagnosis, a short
test asks JARVIS for one on that topic, a reading opens the library.

### Histology practicals from the student's own pages

A *specimen* is a rectangle drawn on a page of an imported document ("Histoloji
örneği seç" on the library page): the crop is rendered afresh from the PDF at
2× and cached in the store's `media` table; the page itself is not touched, and
no microscopy image is ever generated. A specimen's name and features are
recorded with their *basis* — the student confirmed them, or they come from a
caption on the page; the vision pass's description is kept as a description
and never becomes the answer. Stain and magnification stay "bilinmiyor" until
recorded. Only a specimen with an adequate basis is *eligible* for a scored
practical; the rest are study-only and say so. The source document's hash is
kept so a changed or deleted document is reported on the specimen.

A session (study, or timed at 15–300 s, default 60) shows up to ten
specimens, least-seen first and never the same image twice, hides the answer
until it is given, and records two outcomes: the identification (name or
Latin, matched with Turkish/Latin folding) and the explanation (assessed by
one model call for scored items: specific, partial, generic, wrong; unassessed
without a model). A timed answer that runs out counts as a blank. Exposures are
tracked and the results say when repeated specimens inflated the score. Scored
answers feed mastery and the understanding events; "Karıştırılanlarla kıyasla"
puts the recorded features of confusable specimens side by side.

### Source support, the student's flag, and invalidation

Every generated question with a source passage gets a *support status*:
`source_supported`, `needs_review`, `conflicting_evidence`,
`insufficient_evidence`, `unresolved` (the model was unavailable or the batch
limit was reached), `stale` (the source changed), `unavailable` (the source
was deleted), `not_applicable` (no passage: study content), `imported` (a
professor's or the student's own key, kept untouched and never reviewed),
`invalidated`. With `JARVIS_MEDICAL_SOURCE_REVIEW` on (the default) a new
paper is gated: at most twelve items are reviewed by one bounded model call
each (`SUPPORT_REVIEW_SCHEMA`), the source-supported ones are kept and the rest
stay in the bank with their status, and the paper's notes say how many were
left out and why. The bank shows the status on every question ("puansız" when
it is not source-supported) with "Kaynağı incele" to review or re-review.

"Soruda hata olabilir" — in the runner, in the results and in the bank — files
a flag of one of four kinds (disputed key, several defensible answers, source
mismatch, figure problem) with an optional note; it never edits the question.
"Geçersiz say" (confirmed, with a reason) keeps the attempt as history, marks
the question invalid, subtracts its answers from mastery, withdraws the
understanding evidence that rested on it and keeps it out of new papers.

### Storage

Schema version 2 adds a `records` table (kind, subject key, JSON body, created
and updated stamps) and a `media` table for rendered crops; the migration runs
once on open and is recorded. Record kinds: `understanding_event`,
`misconception`, `repair`, `understanding_check`, `prerequisite`,
`prerequisite_diagnosis`, `support_review`, `question_flag`, `study_plan`,
`plan_activity`, `study_log`, `histology_specimen`, `histology_session`.
Every submission with an id is idempotent; a second call returns the stored
record.

## Safety and privacy

- Educational by default. Ordinary anatomy, histology or exam questions get
  no disclaimer. Only a request about the student's own symptoms or personal
  medication draws one short line pointing at clinical care.
- Study material is **not** written to personal memory: every academy turn
  sets `suppress_memory`, and the response metadata says why.
- The academy store, imported document copies and rendered pages live below
  `JARVIS_MEDICAL_DIRECTORY` on this machine. Nothing is uploaded anywhere the
  existing provider policy does not already allow.
- The four medical tools are READ_ONLY except `medical_open_anatomy` (LOW),
  and they go through the same permission engine as every other tool.

## Latency

The augmenter runs on every general turn, so its cost is the cost of the
whole assistant. Measured on this machine, per request:

| Stage | Before | After |
| --- | --- | --- |
| `find_in_text` (1449 aliases) | 115–150 ms | 0.06 ms |
| `ConceptGraph.find` | 10–12 ms | under 0.1 ms |
| `Curriculum.search` | 7.9 ms | under 0.3 ms |
| Full `parse` | 130–170 ms | 0.46–1.28 ms |

Aliases are indexed by their first token (with every valid Turkish suffix
strip), patterns are compiled once and cached, and the curriculum's and the
concept graph's token sets are computed at load time.

## Settings

See `docs/CONFIGURATION.md` for the `JARVIS_MEDICAL_*` variables.

## Not yet built

- No 3D asset ships inside the repository. The BodyParts3D archive is
  downloaded by the student and imported with `scripts/import_bodyparts3d.py`;
  until then the lab is schematic. The importer ships mappings for five scenes
  (the right upper limb, the braincase, the full cranium, the vertebral column
  and the thoracic cage); another region needs a mapping of its own.
- Peripheral nerves have no mesh in BodyParts3D 4.0, so nerve cards keep the
  schematic map, and pins derived from the shape stay marked approximate until
  someone who knows confirms them by hand.
- Covered: the head and neck (skull, cranial nerves, craniovertebral and jaw
  joints, carotid system), the trunk (vertebral column, thoracic cage,
  abdominal wall) and both limbs (bones, joints, muscles, nerves, vessels).
  Not covered yet: the pelvis and perineum in detail, the visceral organs
  (heart, lungs, abdominal viscera), the muscles of the face, and the
  intrinsic muscles of the hand and foot. The dated entries in
  `docs/PROJECT_STATE.md` record each region as it was added.
- Image-based question *generation* needs an image whose provenance is known;
  imported image questions keep their picture reference but new items are
  written as text.
- Movement animation is deliberately absent: without proper anatomical
  rigging it would be a plausible lie. The lab shows the plane, the axis and
  the muscles instead.
- The narration does not listen while it speaks: interrupting by voice would
  mean the microphone hearing the speakers. The student pauses with a button,
  a typed question, Space, or at the checkpoints where JARVIS listens on
  purpose.
- Presentations are read only through the installed PowerPoint; there is no
  bundled renderer, so a machine without Office keeps its decks as PDFs.
- The study workflow's model steps — the reasoning verdict, the diagnostic
  question, the repair explanation and transfer question, the source review
  and the histology explanation grade — need the provider; without it each
  records what it could not do and the flow continues. Source review at
  generation spends up to twelve extra calls per paper, which matters on the
  free-tier quota; `JARVIS_MEDICAL_SOURCE_REVIEW=false` turns it off and every
  new question then stays `needs_review` and unscored until reviewed by hand.
- A plan's weights and priorities have no editor yet (the defaults apply);
  a proposed scope is confirmed as a whole or replaced by creating the plan
  with subjects chosen. A reading activity opens the library rather than a
  particular page, because the plan knows topics, not which page teaches
  them.
- Histology masks (a region hidden on a crop) have store support but no
  drawing tool yet; a specimen needs a rendered page, so a text-only
  document gives no crop. Identification is matched against the recorded
  name and Latin name only — a synonym the student uses is a wrong answer
  until it is recorded as an alias on the specimen.
- The five features were exercised in the native window against Gemini on
  9 September 2026 (plan, "Bugün", a practice sitting with confidence and a
  reasoning, a repair with its transfer question, a histology specimen cut
  from a lecture page and identified in a timed session, the source-support
  gate on a generated paper, a flag and an invalidation). What that pass
  changed is recorded in `docs/PROJECT_STATE.md`. It ran on a copy of the
  study store; the vision pass had described no page in it, so specimens
  there carry no model description.

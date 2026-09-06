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
moves at most one step, and it says what it did.

Wrong-answer choices are counted per concept, so an insight can name the
actual confusion ("Scapula sorularında 2 kez fossa supraspinata seçeneğine
kaydın") instead of a generic encouragement. The wording follows the count: one
observation is reported as one, not as a tendency, and two distractors picked
equally often are both named rather than one of them chosen arbitrarily.

## Anatomy Lab

The lab shows a curated structure card (Latin name, parts, surfaces,
borders, articulations, muscle and ligament attachments, landmarks,
high-yield facts; for muscles origo/insertio/innervatio/functio; for joints
type, surfaces, capsule, ligaments, movements with plane and axis), the
relationship map, movement data and a deterministic landmark quiz. A quiz
distractor is checked against the structure it is asked about: an option that is
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
  until then the lab is schematic. The importer covers the right upper limb;
  another region needs a mapping of its own.
- Peripheral nerves have no mesh in BodyParts3D 4.0, so nerve cards keep the
  schematic map, and pins derived from the shape stay marked approximate until
  someone who knows confirms them by hand.
- The viscerocranium topic exists in the curriculum but has no cards yet; the
  head and neck have no cranial-nerve, vessel or joint cards; the trunk has no
  abdominal-wall muscles and the lower limb no vessels. The coverage audit in
  `docs/PROJECT_STATE.md` lists the gaps in priority order.
- Image-based question *generation* needs an image whose provenance is known;
  imported image questions keep their picture reference but new items are
  written as text.
- Movement animation is deliberately absent: without proper anatomical
  rigging it would be a plausible lie. The lab shows the plane, the axis and
  the muscles instead.

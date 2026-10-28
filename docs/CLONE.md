# Clone a site

An address in, a Next.js project on this PC out. There are three ways to get one, and the first is the one to use; a fourth mode, **Study**, builds nothing and measures a site instead:

| Way | What makes the page | Needs a model | Use it for |
|---|---|---|---|
| **Exact copy** (default) | Chrome's own rendering of the page, frozen; code builds and measures the project | No for the copy; yes for the names and the fixes | A site you own (a migration, lost source code), or to learn how a site is built |
| **From a picture** | A model that sees writes the page region by region from a reference picture; code places, builds and measures it | A model that sees | A design that exists only as an image, with motion added by GSAP |
| **Study** | Code only: Chrome opens the site, a script measures it (Site DNA), Python reads the numbers and composes a master prompt | No | A prompt for your own coding agent (Claude Code, Codex, a local one) to rebuild the look and motion of a reference, with measured values and the reason for every choice |
| **Agent rebuilds it (kit)** | A model follows the skill of [ai-site-cloner](https://github.com/Mahanaicoach/ai-site-cloner) | A strong one: a cloud model, or a local one with a large context | The first version of this page, kept for the models it works with |

Check a site's terms first: its text, logos and brand belong to its owner. This is not legal advice.

## Why the exact copy exists

The first version only had the agent. On 2026-10-25 a 27B model on a 16 GB card ran for 2 h 39 min on brioni.com: 776 tool calls (261
commands, 133 folder listings, 118 file reads, 114 outlines of JSON), 39 files written, the context trimmed 310 times, the model swapped
194 times for the vision model, and **no component built**. Its context was about 27K tokens, 12K of them the kit's skill, and the
room left for one answer shrank to about 1,000 tokens, thinking included, so its tool calls were cut off in the middle. The skill is
written for a model that keeps a whole site in its head; a local model cannot, and no amount of guards around the loop changed that.

The fidelity of a clone is decided by what is copied, not by how well a model describes a layout. So the page is copied as the browser
rendered it, by code, and a model is asked only for what is small and what a number can check.

## Exact copy

```
capture  ->  project  ->  measure  ->  (polisher  ->  measure)
```

It is a workflow like any other (its live graph is in *Sessions*, models go through the gateway, Stop works). The steps are in
`backend/src/agentfit/clone/pipeline.py`; the work is in `capture.mjs`, `replica.py` and `measure.mjs`.

### Capture (code)

Chrome (or Edge) opens the address like a visitor: the same Chrome, introducing itself as "Chrome" (a headless one says
"HeadlessChrome" and bot protection answers 403 to it). Then, at desktop width (1440) and phone width (390):

- the page settles, and is scrolled once from top to bottom so lazy pictures and reveal animations happen;
- **cookie banners and pop-ups are hidden**: the known consent managers, open dialogs, fixed layers over the page that are not its header.
  They are hidden with an attribute, never removed: a page built with React that finds a node of its own gone throws, and its error
  page replaces the site (seen on brioni.com, whose newsletter dialog appears four seconds after the first scroll). They are looked
  for three times, because they come on timers;
- the reference screenshots are taken (full page, animations off);
- the page is **frozen**: the rendered markup, with each element's state (the picture the browser chose, a canvas as a picture, the
  value of a form field), every style rule as the browser holds it (also those a script inserted), and every picture, font and file
  the page used. A web component's shadow DOM is flattened into the page (`:host` becomes a class, `<slot>` is filled with what the
  page put in the host), because `outerHTML` does not contain it and a Swiper carousel is nothing without its shadow styles. An
  `<iframe>` becomes a box of the same size holding a picture of what it showed (a map). Scripts are dropped.
- a font file the page never fetched is not copied (a map's style sheet lists a hundred scripts' worth of subsets: 186 files on
  brioni.com);
- the page is cut into **sections** (the header, each block of `main`, the footer), and each gets a component;
- the document's **compatibility mode** is noted: a page with no doctype is drawn by the browser in quirks mode (an old table-based site
  such as news.ycombinator.com), where a line of small inline text has another height, and Next.js always writes a doctype. Such a
  page is served from `public/clone/page.html`, a document without one, with the same sections (`next.config.mjs` rewrites `/` to it);
  first copied as a normal Next.js page, Hacker News measured 78.7%, with rows 4 px taller from the third on, and 100.0% after;
- the sections are frozen again at phone width. A site often sends a phone other markup and other pictures (brioni.com: a different
  header and a different hero picture), so a section whose phone markup differs has both, and a media query shows one of them (at the
  width the page's own styles use most: 640, 768 or 1024).

### Project (code, and a model for the names)

`replica.py` writes a Next.js project:

```
src/app/layout.tsx          the document: title, description, icon, the classes of <html> and <body>, the two style sheets
src/app/page.tsx            the page: the shell of the document, and each section as a component
src/components/sites/<site>/<Name>.tsx    one file for each section
public/clone/site.css       the page's own style rules, with their addresses made local
public/clone/fixes.css      empty: where fixes go (loaded after site.css)
public/clone/<files>        pictures, fonts, the pictures of frames
docs/research/<site>/capture, report      the screenshots and what the measure found
```

The style rules are one file linked by the layout, not run through the framework's CSS pipeline, which rewrites what it does not know:
a copy is a copy only if the rules reach the browser unchanged. The markup is real JSX (`jsx.py`): `class` is `className`, a style
is an object, SVG attributes are camelCase, a form control's `value` is a `defaultValue`, text with a brace is a string. The generated
files start with `// @ts-nocheck`, because a copy keeps every attribute of the original, whatever React's types say of it; remove the
line when you tidy a file.

The model, when it is there, only **names** the components (`Header`, `HeroTravelJacket`, `ProductCarousel`): it is asked once, its
answer is validated (a name must be PascalCase, 3 to 40 letters, unique), and what is not usable falls back to a name made from the
section's tag, class and heading. A model that fails changes nothing but the names. Then `npm install` and `npm run build`.

### Measure (code)

The built project is served in production mode on 127.0.0.1 and opened by the same Chrome at the same widths. Both pictures are full-page
screenshots. No model judges; the numbers come from the two pictures:

| Number | What it is |
|---|---|
| **pixels** | the share of pixels whose colour is within 16/255 of the original's (the part missing from a shorter page counts as wrong) |
| **ssim** | structural similarity of 8×8 blocks of the grey picture: is the same thing drawn in the same place |
| **visual** | `(pixels + ssim) / 2` |
| **layout** | how close the height of the page, and of each section, is to the original's |
| **text** | F1 over the multiset of the words the original shows and the clone shows (desktop only) |
| **accuracy** | the mean of *visual* over the widths: the headline |

Each section is also scored on its own, aligned by the section's own top, so a section that moved the others down is not blamed for
them. The report is `docs/research/<site>/report/report.json`, with the original and the clone at half size and a picture of where
they differ (`diff-*.png`, red).

The target is **80%**: below it the run ends *blocked* with the reasons instead of *completed*. Below **97%**, if a section scores under
90%, the polisher is called.

### Polisher (a model, only when needed)

An agent with a short prompt and the file tools (and `look`, if a model that sees is available). It reads the weak sections from the
measure and writes **style rules only** to `public/clone/fixes.css`, selecting a section by `[data-clone-section="N"]`. The measure
runs again; at most two rounds.

### What it gave (2026-10-27, no model for the copy)

| Site | Time | Sections | Accuracy (desktop, phone) |
|---|---|---|---|
| brioni.com/it/it | 65 s | 12 | **99.9%** (99.9%, 99.8%), text 98.9%, layout 100% |
| python.org | 51 s | 4 | **99.9%** (100.0%, 99.9%) |
| news.ycombinator.com | 46 s | 2 | **100.0%** |

The time is the capture (23 to 33 s), `npm install` and `npm run build` (about 20 s), and the measure (about 11 s). No console error and
no failed request on brioni.com. The Next.js project built and served by `next start` was the one measured, not an intermediate page.

### Known limits

- **What the page does is not copied**, only what it shows: a menu does not open, a carousel does not move, a form does not send.
  Its scripts are dropped on purpose (they are the framework of the original, not code of yours).
- A video is a still of its first frame if the file is too big to copy (20 MB); WebGL and canvas art is a picture of the moment of the capture.
- Scroll-linked motion (pinned sections, parallax) is frozen at the top of the page.
- A page that sends different markup for a tablet gets the desktop one between the phone breakpoint and 1440.
- A site behind a login or a captcha cannot be captured: the run ends and says what the site answered. Stronger protection than a
  user agent can still refuse the capture.
- The accuracy compares the page with its own screenshot at two widths, not with every width.

## From a picture

```
prepare -> analyze -> compose -> measure -> (refine -> measure)* -> motion -> reviewer
```

A frontier model can be given a whole brief (a hero that looks like this picture, with parallax, hover and scroll) and write it in one
long conversation. A 27B model has about 27K tokens, so the brief is cut into the phases it describes, each small enough, and code
keeps the numbers (`picture.py`, `picture.mjs`):

1. **prepare** (code): the project (Next.js and GSAP), the reference picture in `docs/reference/`, the picture's colours and bands
   (`picture.mjs`), the **skills** copied into `.skills/`, `npm install`.
2. **analyze** (the model that sees): writes the composition as data: regions with a kind (photo, text, ui, shape), a place and a size
   in percent, a depth, a rotation and the text in them. It is clamped and checked; if it is unusable the bands of the picture stand in.
3. **compose** (the model): one component for each region, looking at that region's **crop**. The place of a region comes from the data,
   not from the model, so a wrong size cannot move the layout. A component with an import, a script, `fetch`, `window` or `document` is
   refused; one that does not compile is asked for again with the compiler's words, then replaced. A photograph is the crop itself, and
   so is a region the model could not write: the report says how many.
4. **measure** (code): the page is built, shown at the picture's size and compared with it, region by region. The report separates the
   accuracy of the whole picture from the accuracy over **the regions the model wrote**, and says which share of the picture is its own
   crops, so that a page made of photographs cannot pass for a built one.
5. **refine** (the model): the weakest regions (under 85%, three at most, two rounds) are written again from the crop, the clone's own
   crop and the score. The new code stays only if the next measure is better; if not, the old code comes back.
6. **motion** (the model chooses numbers; a fixed component animates): the model reads the brief, and two sentences of the skills copied
   into the project (the design skill's on using motion sparingly, the GSAP skill's on how steep each ease is: `GUIDANCE` in
   `picture.py`, "it says how to choose, never whether"), and answers with a few numbers (the style and length of the intro, how far
   the mouse parallax goes, the depth of the hover shadow), clamped to what the component allows. The step's result says which skills
   it was given (`guided_by`). The component is a GSAP one written from the GSAP skills (`gsap-react`: one `gsap.context`, reverted when the component goes;
   `gsap-performance`: transforms and opacity only), animates the *content* of each region so that its box keeps its place and
   rotation, clears everything it set when it ends, waits for the intro before answering the pointer, and does nothing for a visitor who
   asks for reduced motion. The page is measured when the intro is over and it stays only if it still matches the picture, within 3
   points. A first version let the model write the GSAP code itself: it moved the page at rest (87.8% fell to 64.9%) and was
   removed by the measure, which is the rule working and a useless result; numbers in a fixed component cannot leave the page moved.
7. **reviewer** (an agent): the report for the person, from the numbers.

## Agent rebuilds it (the kit)

It is the workflow of [Mahanaicoach/ai-site-cloner](https://github.com/Mahanaicoach/ai-site-cloner) (MIT) run the way it runs in a
coding agent in the terminal, with the model you choose: the kit's project goes in a folder of yours or in a copy of a GitHub
repository, its packages are installed, and the model follows the kit's `/clone-website` skill. It opens the site, finds the pages and
**asks you which ones to clone**, records the site with the kit's scripts, writes a spec for each section and builds it, checking each
against the original. The kit is fetched from GitHub on your PC (`git fetch` of the pinned commit, which Git checks against the hash).
Its tools (`browser_*`, `download_asset`, `look`, `run_command` with a fixed list of commands, `json_outline`, `preview_start`,
`ask_user`, and the file tools) and its guards (passes of 150 rounds, the reading tools taken away after 20 rounds without a change
in the project) are in `studio.py`, `tools.py` and `prompts.py`. Use a model with at least 32k tokens of context, or a cloud model.

## Master prompts

A library of prompts for a coding agent that builds or restyles a website: one Markdown file for each prompt in
`backend/src/agentfit/clone/prompt_library/`, each about one thing (a gallery with filter chips, a card that plays a video on hover,
smooth scroll with Lenis and GSAP, how to study a reference, a look-and-fix loop). The text of a prompt is English because it is sent to a
model; its title and summary carry their Italian in the file (`title_it`, `summary_it`). The rules every prompt shares (the gates, the asset
policy, the hard lines, the checks, what to deliver) are written once in `shared.md`. The prompts are original: nothing is copied from sceneai.art
or from the examples and media of MotionSites.

**Shipped today: five pilot prompts**, B01 (gallery with chip filters), B02 (card hover video), C01 (smooth scroll, Lenis and GSAP), F01 (study a
reference grammar) and F03 (look and fix loop). The plan is fifty; **the other 45 are planned, not shipped**, and nothing in the program says
otherwise. Each file is checked when the library loads (`clone/library.py`): every problem is reported at once, a file with a byte order
mark or CRLF line endings loads and hashes the same, a recipe is at most 6,000 characters and `shared.md` at most 3,000. A prompt records the
libraries it names with the version that was looked up, and the skills it cites (`taste-skill`, `motionsites-web-builder`, `impeccable`,
`playwright-cli`). `proofs.json` records the prompts that were tried with a real builder; it is empty now, so every card says "Not tested yet".
A prompt is not claimed to work until a builder has used it and a measure says so.

The **Master prompts** tab lists the library (`GET /api/clone/prompts`, `GET /api/clone/prompts/{id}`) and copies one prompt on its own, with every
placeholder written `UNKNOWN`. From a study the tab composes one prompt (below). `/api/clone/prompts*` is registered before `/api/clone/{study_id}`.

### The composer

`clone/compose.py` is code, not a model. A table of rules says what the Site DNA must show to recommend a recipe (today three rules: B02 when the
page has a gallery of looping muted videos, B01 when it has chip filters, C01 when it uses Lenis); F01 and F03 are always in. `compose` writes the
opening, one `## Site DNA` block, the chosen recipes and the shared rules, and returns the reason for each recipe, the ones it dropped and the token
count. At most six are recommended; the prompt is at most 12,000 estimated tokens (characters divided by four); when it is over, recipes are dropped
from the end of the order and named, and if the two that are always in still do not fit, the answer says `over_budget`. The measured values are
stated **once**, in the Site DNA block: a placeholder in a recipe becomes "see Site DNA, palette". A value that was not measured reads `UNKNOWN`;
the composer never invents one. `POST /api/clone/{id}/compose` composes (the recommendation, or exactly the prompts in `picked`; an unknown id is
a 400), and `POST /api/clone/{id}/prompts/save` writes `docs/prompts/<host>-master.md` (never over an existing file: `-2`, `-3`) and copies the
skills the prompts cite into the project (`.skills/` and `.claude/skills/`). Measured on four sites: 4,003 to 6,676 tokens.

## Site DNA

Choose **Study** and give an address. A model-less run (`clone-site-study`, `clone/dna.py`, started by `studio.start_study`) does two things.
`clone/dna.mjs` opens the page like a visitor with the app's own browser engine, scrolls it once, measures it, reads the page's scripts to spot
libraries, probes scroll and hover, and writes `dna-raw.json` and a thumbnail; it only counts, it interprets nothing and it never reads the page's
words. `dna.py` then reads the numbers as the **Site DNA** (`GET /api/clone/{id}/dna`): the palette with roles, the fonts, the type scale, spacing,
radii, shadows and container, the sections and their roles, the videos and canvases, the motion (transitions, keyframes, sticky and fixed elements,
smooth scroll, scroll snap, view transitions), the patterns (chip filters, dialog, carousel, masonry, sticky header, newsletter form) and the
libraries found, each with its evidence. The libraries come from `dna_signatures.json`, plain regular expressions over the scripts the page loaded;
a word that only appears inside a bundle does not make a library (LESSONS.md). A value that could not be measured is `null`, and a `limits` list
says why (a script that could not be read, a page with no sections, a probe that was skipped, a slow visit). A page that is a canvas or an iframe
gets no sections and says so, and a site that answers 403 or a captcha ends the run with what the site said, not an empty DNA. The DNA also says
what it does not cover: layout, text and the feel of the motion.

Stage budgets are in `dna.mjs`: the scroll pass stops at 25 s, reading scripts at 60 s, a probe at 10 s, and a run is cut at 300 s; what a stage
did not finish is reported as a limit. Real runs (2026-10-28, this PC, an isolated data folder): recent.design, footer.design, inspora.design and
sceneai.art took about 31, 14, 29 and 16 seconds. The interface (DNA card, composed prompt, reasons, Save) was run on recent.design (about 5,300
tokens; Save wrote the prompt and the Playwright skill into a project). **Not run:** a site that fails during the study in the interface, the messages
for a dropped recipe or a prompt over budget, the Install skills button, saving into a chosen clone, the English interface, and a builder that
follows a composed prompt (Task 22 of the plan, not done at the time of writing). CI's backend job has no `desktop/node_modules`, so the test that
parses the library with TypeScript is skipped there; it runs in `scripts/check.py` on a PC with the dependencies, and the pinned hash of the library
still guards the files in CI.

## Skills

The skills of a coding agent, fetched from GitHub on this PC at an exact commit and kept, with their authors' licences, in
`%LOCALAPPDATA%\Will It Run\Data\tools\skills\<owner>__<repo>@<commit>` (`clone/skills.py`; none is bundled with the program):

| Pack | Repository, commit | Licence | Skills |
|---|---|---|---|
| gsap | greensock/gsap-skills `aed9cfd32777` | MIT | gsap-core, gsap-timeline, gsap-scrolltrigger, gsap-plugins, gsap-utils, gsap-react, gsap-performance, gsap-frameworks |
| frontend-design | anthropics/skills `dbd4588f9e10` | Apache-2.0 | frontend-design |
| vercel | vercel-labs/agent-skills `6ec269328bc4` | MIT | react-best-practices, web-design-guidelines |
| threejs | cesartevisual/threejs-skills `84f9bcb4bea1` | MIT | threejs-web, -scene, -materials, -lighting, -assets, -animation, -interaction, -r3f, -html, -performance, -testing |
| playwright | microsoft/playwright-cli `b1a5903c5b63` | Apache-2.0 | playwright-cli |
| taste (design) | Leonxlnx/taste-skill `12af471f627f` | MIT | taste-skill |
| motionsites (design) | Motionsites-AI/claude-skill `faa30d512e39` | MIT; its examples and media are MotionSites' and are not kept | motionsites-web-builder |
| impeccable (design) | pbakaus/impeccable `3e3a9179e01a` | Apache-2.0; reference/ios.md and android.md are ehmo/platform-design-skills (MIT) | impeccable |

The first five are the **picture** packs (`purpose="picture"`): what a build from a picture fetches and copies without being told which. The three
**design** packs (`purpose="design"`) are the skills the master prompts cite. They are asked for by id; `install_into` copies them into a project's
`.skills/` and `.claude/skills/`, where your own coding agent finds them, and returns what it wrote, what it skipped and what failed. A folder of your
own with the same name in `.claude/skills/` is never touched: the ones this program writes carry a marker file, `.willitrun-skill`. A design pack keeps
text and data only. Git takes the whole commit and the copy drops what is excluded, so "not kept" is the exact word: impeccable downloads 4,423 files
(86 MB) to keep 0.46 MB, MotionSites 2.58 MB for 0.6 MB, taste 3.09 MB for 2.0 MB. The texts of these skills still mention `scripts/`, `examples/` and
`media/` (impeccable runs `scripts/impeccable context`; MotionSites opens `examples/INDEX.md` and runs `scripts/shoot.py`): those are not copied, and
the master prompts never tell a builder to run or open them.

`GET /api/clone/skills` lists them and `POST /api/clone/skills/install` fetches them in the background; a build from a picture
fetches them by itself when none is on the PC (Git is needed), and they are fetched once: later runs only copy. A run copies the skills
it wants into its project (`.skills/<name>/`), for whoever continues the work there. The model of a step is **not** given a skill:
that would fill the context of a small model, and the skills are written for a coding agent that writes GSAP and designs a page from
a free brief, while here the motion is a fixed template and the look is a picture. A step is given the few sentences of a skill that
bear on its one choice (`SkillStore.excerpt`, found by a phrase, which is stable because the commit is pinned). Today that is the
motion step only; composition and refinement get none, because the design skill tells a model to take aesthetic risks and what they
write is a copy. The exact copy uses no skill. The Playwright CLI itself is not installed (no global npm package): the app drives the
browser with its own scripts. (`npm install -g cli@latest`, in the script these skills came from, installs an unrelated package named
`cli`; the Playwright one is `@playwright/cli`.)

## Where the project goes

| You choose | The project is built in |
|---|---|
| nothing | a folder of its own in Will It Run's data (`workspaces\clone-<id>`), removed with the clone |
| an empty folder | that folder (the agent: also a project of the kit) |
| any other folder | a subfolder named after the site |
| a GitHub repository | a copy of it in Will It Run's data (`workspaces\repos`); nothing is pushed unless you approve it |

A folder or repository of yours is never deleted with the clone.

## Getting ready

The browser engine is installed up front: Playwright's core library (`playwright-core`, pinned), in
`%LOCALAPPDATA%\Will It Run\Data\tools\clone-browser`, by `npm install --prefix`. It drives the Chrome or Edge you already have, so
nothing big is downloaded. It needs Node.js 22 or newer and npm; the agent also needs Git, to fetch the kit.

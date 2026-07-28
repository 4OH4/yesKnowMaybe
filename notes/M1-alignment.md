# M1 — span alignment rates across the M0 corpus

Measured with `dikms align <source_id> --dry-run` against the M0 extraction
output in `output/*.json`, at the shipped floor (`alignment.min_ratio: 0.95` in
`config/extraction.yaml`). "Facts" counts entities, relations and type
proposals — everything carrying a `Provenance`.

This is the M1 exit criterion for span alignment: the rate has to be
*understood*, not merely produced.

## Results

| Source | Format | Facts | Aligned | Rate | Unresolved |
|---|---|---:|---:|---:|---:|
| `navalnews-fads-type83-2025` | HTML (trafilatura) | 69 | 69 | **100.0%** | 0 |
| `mod-digital-strategy-2021` | PDF (pypdf) | 487 | 477 | **97.9%** | 10 |
| `defence-investment-plan` | PDF (pypdf) | 1735 | 1674 | **96.5%** | 61 |

By match method:

| Source | exact | normalised | fuzzy |
|---|---:|---:|---:|
| `navalnews-fads-type83-2025` | 68 | 0 | 1 |
| `mod-digital-strategy-2021` | 293 | 182 | 2 |
| `defence-investment-plan` | 1100 | 569 | 5 |

Only **8 facts in the whole corpus** (0.4% of the 2,220 aligned) rest on the
fuzzy tier; everything else matches character-identically after normalisation.
All 8 were inspected individually and all 8 are correct.

The HTML/PDF split predicted in the ticket shows up, but weakly and not for the
predicted reason. PDF artefacts (line-break whitespace, `ﬁ`/`ﬃ` ligatures,
hyphens at line breaks, typographic quotes and dashes) account for the whole of
the exact→normalised shift — 38% of MOD facts and 32% of DIP facts need
normalisation where the HTML page needs almost none — but they cause hardly any
*failures*. Normalisation absorbs them.

### Hyphens at line breaks: the expected artefact isn't the real one

Worth recording because it inverts the obvious assumption. PDF text wraps at
hyphens for two indistinguishable reasons: a compound breaking at its own hyphen
(`dual-\nuse`), or a word hyphenated purely to wrap (`capa-\nbility`). The first
implementation assumed the second and dropped the hyphen.

Counting every such site in both PDFs: **40 of 40 are compounds** — `dual-use`,
`AI-enabled`, `stand-off`, `next-generation`, `anti-submarine`,
`cross-government`, and one URL (`open-\ngovernment-licence`). **None** is a
true wrapped word. Dropping the hyphen therefore corrupted 40 compounds into
`dualuse`/`standoff` to rescue nothing, pushing each into the fuzzy tier where
it scraped past the floor at ~0.98.

Keeping the hyphen and swallowing only the break moved **DIP fuzzy 15 → 5 and
MOD fuzzy 5 → 2**, all into character-identical `normalised` matches at ratio
1.0, with **no change to any alignment rate**. Same spans, two-thirds fewer of
them depending on the narrow fuzzy floor — the cheapest hardening available
against the margin problem described below. A genuinely wrapped word now
degrades to a fuzzy match instead (one retained hyphen costs little ratio on any
quote of reasonable length), or to UNRESOLVED — never to a wrong span.

Two subtleties, both caught by property tests rather than by reading:

* **Only true hyphens may rejoin, not en/em dashes.** All three flatten to `-`
  for matching, so keying the rule off the flattened character glued
  `"a clause —\nthen more"` into `"a clause -then more"`. The rule now tests the
  *source* character's class.
* **The rule must look only forwards.** An earlier version also checked the
  character *before* the hyphen, which can lie outside a candidate span — making
  `normalise(slice)` differ from `normalise(whole)` and quietly breaking the
  re-verification step that the whole design rests on. Both tests are now local
  to the slice.

## Why the unresolved cases fail

Every unresolved case was inspected. None is a normalisation gap; they fall
into three groups, and in all three refusing to align is the correct answer.

1. **Quotes stitched from non-contiguous text (the large majority).** The model
   returns one "quote" assembled from separated fragments, either explicitly
   elided —
   `"current and future warhead...programmes"` — or implicitly, by gluing a
   lead-in to a distant list item:
   `"invest in the following capability areas through the Defence Investment
   Plan: • Reactor core"`, or a table header to a distant row:
   `"Table 2: Spending on the Integrated Force12 … Weapons and Munitions
   £11.1bn"`. There is no single contiguous span in the source that this text
   corresponds to, so there is nothing to highlight. **54 of DIP's 61**
   unresolved facts and **5 of MOD's 10** are this pattern (counted by the
   presence of an ellipsis, a bullet, or two or more internal line breaks).

2. **Quotes taken from outside the chunk the fact was attributed to.** e.g.
   `"Defence Nuclear Enterprise's (DNE)"` and `"Defence Science and Technology
   Laboratory (DSTL)"` appear in the DIP, but not in the chunk whose extraction
   produced the fact. Alignment is deliberately bounded to the originating
   chunk, so these are refused. Worth noting as model behaviour: the quote is
   not always grounded in the text the model was actually shown. 2 in DIP,
   1 in MOD.

3. **Paraphrase below the floor.** A handful of short quotes where the model
   dropped or added words: `"the Commando Force"` against the source's
   `"the Army, Commando Force, and Special Forces"`, or `"agreed with HM
   Treasury"` against text broken by a footnote marker. Short quotes give the
   ratio little to work with; these sit just under the floor and are refused.
   5 in DIP, 4 in MOD. Quotes shorter than `alignment.min_fuzzy_chars` (12) are
   not fuzzy-matched at all; at the shipped 0.95 floor that rail currently
   changes nothing on this corpus (the ratio rejects them first) and is kept
   only so lowering the floor cannot start snapping short names onto
   near-neighbours.

Group 1 is an **extraction-prompt** finding, not an alignment defect — the
prompt does not currently forbid elided or stitched quotes. Tightening it
("quote a single contiguous run of source text verbatim") would plausibly move
DIP's rate to ~99%. That is out of scope here (the ticket explicitly excludes
changing the extraction prompt) and is worth its own backlog item.

## Setting the floor: 0.95, from evidence

The floor was initially set at 0.85 and moved to 0.95 after inspecting every
fuzzy match on the real corpus. Rates by floor:

| Floor | HTML | MOD | DIP |
|---|---:|---:|---:|
| 0.85 | 100.0% | 97.9% | 97.8% |
| 0.90 | 100.0% | 97.9% | 97.2% |
| **0.95** | **100.0%** | **97.9%** | **96.5%** |
| 0.97 | 100.0% | 97.7% | 96.3% |

The raw rate is not the thing to maximise. 0.85 buys 22 extra DIP matches
(1.3pp), and they split two ways:

* **9 are wrong spans.** These are the group-1 stitched quotes, where a long
  shared lead-in carries the ratio while the tail names a different item, so
  `"…through the Defence Investment Plan: • Astute Class"` aligns at 0.9462 onto
  a span ending `"• Vanguard Class"`. A highlight pointing at the wrong bullet
  is worse than no highlight, and directly contrary to "no unaligned or
  unverified offset is ever stored as if it were a confirmed span". These are
  what the floor exists to exclude.
* **13 are partial spans** — a contiguous prefix of the quote, every character
  of which really is text the quote contains (`"substantial continued
  investment in"`, `"…for the Maritime Domain"`). Losing these is a cost of the
  higher floor, not a benefit.

(Classified mechanically: a span is *partial* if its normalised text is a
substring of the normalised quote, *wrong* if it contains text the quote does
not.)

On this corpus the two populations do separate, but not by much:

* the lowest-scoring **correct** fuzzy match is **0.9506** (DIP:
  `"Uncrewed Systems Centre (USC)... will be Defence's central authority…"`,
  whose span correctly covers the whole contiguous region including the elided
  words);
* the highest-scoring **wrong** match is **0.9462** (the Astute/Vanguard case
  above).

So the usable gap is about **0.004 wide**, and 0.95 sits inside it. Most correct
fuzzy matches score far higher (0.97–0.99) and are line-break artefacts inside
otherwise identical text, but the two lowest accepted matches are word-level
differences, so the margin should not be treated as comfortable. The
de-hyphenation fix above reduces how much rides on it — two-thirds of the former
fuzzy population now matches exactly, leaving 8 facts corpus-wide inside the
fuzzy tier — but it does not widen the gap itself.

**Known limitation of the mechanism, not just the value.** A single ratio is a
blunt instrument for exactly this failure mode: a long shared prefix can carry a
divergent tail, and the separation above is a property of this corpus rather
than a guarantee. Two consequences are already visible:

* one stitched DIP quote (`"…: • SSN-A"`, 0.9634) is accepted, and its span
  covers only the contiguous lead-in, stopping at the bullet rather than
  running on to the wrong item. A partial span is defensible — every character
  it selects is text the quote really contains — but it is not the whole quote;
* if a future document produces correct matches below 0.95 or wrong ones above
  it, the fix is a better scoring rule (scoring the tail separately, or
  bounding the longest contiguous mismatch), not more floor-tuning.

Flagged rather than pre-emptively built.

## Ambiguity

Quotes matching more than once inside their own chunk are common where the quote
is just an entity name: 4 occurrences in the HTML page, 40 in MOD, 135 in DIP —
`"Defence"` alone matches 19 times in one MOD chunk. The first match within the
chunk bounds is used and the ambiguity is reported by `dikms align`, not
silently resolved. (These counts rose from 3/35/95 once occurrences were counted
on the normalised text rather than the raw text — a quote appearing once
verbatim can appear several times once typography is flattened, and the raw
count was calling those unambiguous.)

Two limits worth knowing before ingestion consumes this:

* ambiguity is **reported, not persisted** — it is in the CLI summary, not in
  `output/<id>.json`, so a downstream reader cannot tell a uniquely-located span
  from one of 19 candidates. Making it visible in the store means a
  `Provenance` field, which is a schema decision for the ingestion ticket rather
  than something to add here (backlogged);
* occurrence counting is done on the *normalised* text at every stage, including
  when the exact stage wins — a quote occurring once verbatim can still occur
  several times once typography is flattened, and counting raw occurrences would
  have called that unambiguous. A fuzzy match still reports one occurrence by
  construction, since there is no cheap way to ask "how many other regions would
  also have cleared the floor". Fuzzy is now 0.4% of aligned facts, so this is a
  small blind spot, but it is a blind spot.

The practical mitigation is a longer quote, which again points back at the
extraction prompt rather than at alignment.

## Reproducing

```
uv run dikms align navalnews-fads-type83-2025 --dry-run
uv run dikms align mod-digital-strategy-2021 --dry-run
uv run dikms align defence-investment-plan --dry-run
```

Runs offline against `data/corpus/` and `output/*.json` — no credentials needed.
Drop `--dry-run` to write the verified spans back into the output file.

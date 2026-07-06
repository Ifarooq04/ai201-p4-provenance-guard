# ai201-p4-provenance-guard
# Provenance Guard

A backend system that classifies submitted creative content as likely AI-generated, likely human-written, or uncertain — with confidence scoring, transparency labels, and an appeals workflow for contested classifications.

## Architecture Overview

A submission flows through the system as follows: a POST request to `/submit` first passes through a rate limiter (Flask-Limiter), which blocks abuse before any real processing happens. The raw text is then run through two independent detection signals — an LLM-based classifier (Groq) and a stylometric heuristics function (pure Python) — each producing a score between 0 and 1. Those two scores are combined into a single confidence score using a weighted average. That confidence score is mapped to one of three transparency labels, and the full result (both signal scores, the combined score, the label, and metadata) is written to a structured audit log before being returned to the creator as JSON.

Appeals follow a simpler, separate path: a creator submits a `content_id` and their reasoning to `/appeal`, the system looks up the original log entry, flips its status to `"under_review"`, and appends the appeal reasoning to that same entry — so a human reviewer sees the full history (original classification + appeal) in one place.
SUBMISSION FLOW
Creator → POST /submit → Rate Limiter → Signal 1 (LLM) + Signal 2 (Stylometrics)
→ Confidence Scoring → Label Generation → Audit Log Write
→ Response {content_id, attribution, confidence, label}
APPEAL FLOW
Creator → POST /appeal {content_id, creator_reasoning} → Look up content_id
→ Update status to "under_review" → Audit Log Update
→ Response {status, content_id, message}

## Detection Signals

**Signal 1: LLM-based classification (Groq — llama-3.3-70b-versatile)**
Sends the submitted text to Groq with a prompt asking it to assess whether the text reads as human- or AI-generated, returning a score (0–1) plus a short justification. This captures holistic semantic and stylistic coherence — things like hedging language, overly balanced argument structure, and unnaturally even tone that AI models tend to produce.

*Blind spot:* it's a black box — we can't fully audit its reasoning, and it can be fooled by lightly-edited AI text or penalize human writers whose natural style happens to sound formal or polished (e.g., non-native English speakers, academics).

**Signal 2: Stylometric heuristics (pure Python)**
Computes structural/statistical properties of the text — sentence length variance, type-token ratio (vocabulary diversity), and punctuation density — and combines them into a single score. AI-generated text tends toward uniformity: similar sentence lengths, safer/more repetitive vocabulary, and more consistent punctuation patterns. Human writing tends to be messier.

*Blind spot:* it's purely structural and has no sense of meaning. A human writing in a deliberately repetitive or simple style (children's writing, technical documentation, minimalist prose) can score identically to AI output.

**Combining the signals:** `confidence = (0.6 × llm_score) + (0.4 × stylometric_score)`. The LLM signal is weighted higher since it's semantically aware and generally more reliable standalone; stylometrics acts as a supporting, fully-transparent check.

## Confidence Scoring

Confidence is a float 0–1, mapped to three bands:
- **≥ 0.75** → Likely AI-generated
- **0.35 – 0.74** → Uncertain
- **≤ 0.34** → Likely human-written

This wide "uncertain" band is intentional: on a creative platform, a false positive (calling a human's work AI-generated) does more damage to trust than a false negative, so the system is deliberately biased toward caution rather than confident calls in ambiguous territory.

**Validation:** I tested the scoring against 4 deliberately chosen inputs spanning the range:

| Input type | LLM score | Stylometric score | Combined confidence | Label band |
|---|---|---|---|---|
| Clearly AI-generated (formal, hedge-heavy paragraph) | 0.8 | 0.386 | **0.634** | Uncertain |
| Clearly human-written (casual review, typos, personality) | 0.2 | 0.239 | **0.216** | Likely human |
| Borderline: formal human writing (monetary policy paragraph) | 0.7 | 0.421 | **0.588** | Uncertain |
| Borderline: lightly-edited AI output (remote work reflection) | 0.4 | 0.403 | **0.401** | Uncertain |

Two example submissions with clearly different confidence:
- **High confidence:** the casual ramen-review text scored **0.216** (llm_score 0.2, stylometric_score 0.239) — landing solidly in "Likely human-written."
- **Lower/mid confidence:** the AI-generated paradigm-shift paragraph scored **0.634** (llm_score 0.8, stylometric_score 0.386) — landing in "Uncertain" rather than a confident AI call, because the wide uncertain band prioritizes caution.

This is a real, observed pattern in the system: three of the four test cases (including the clearly-AI one) landed in "Uncertain," and only the clearly-human case fell below the threshold. That's consistent with the design goal of erring toward caution rather than false accusations — see Known Limitations below for more on this tradeoff.

## Transparency Label

The three label variants, shown exactly as returned by the API:

| Confidence | Label text |
|---|---|
| **High-confidence AI** (≥0.75) | "This content appears to be AI-generated (confidence: high). Our system detected strong indicators of AI authorship based on writing style and structural patterns." |
| **High-confidence human** (≤0.34) | "This content appears to be human-written (confidence: high). Our system found writing patterns consistent with typical human authorship." |
| **Uncertain** (0.35–0.74) | "We're not confident whether this content is AI-generated or human-written. The creator can appeal this classification if they believe it's inaccurate." |

## Appeals Workflow

Any creator can appeal a classification by submitting their `content_id` (returned from their original `/submit` call) and a `creator_reasoning` text explaining why they believe the classification is wrong. The system:
1. Looks up the original log entry by `content_id`.
2. Updates its `status` field to `"under_review"`.
3. Appends the appeal reasoning and an `appeal_timestamp` to that same entry — so nothing about the original classification is lost.
4. Returns a confirmation to the creator.

Example test — a piece originally classified "likely_human" with confidence 0.185 was appealed with the reasoning: *"I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical."* After the appeal, the log entry showed:
```json
{
  "content_id": "10d1b876-c695-4efa-9793-3e57ee806075",
  "status": "under_review",
  "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker...",
  "appeal_timestamp": "2026-07-06T21:24:46.285252+00:00",
  "confidence": 0.185,
  "llm_score": 0.2,
  "stylometric_score": 0.162
}
```
A human reviewer opening this entry sees the full picture — original text's scores, the label shown, and the creator's explanation — in one place, with no cross-referencing needed. Automated re-classification is intentionally out of scope; a human makes the final call.

## Rate Limiting

Limit: **10 requests per minute, 100 per day**, applied via Flask-Limiter on `/submit`.

**Reasoning:** A real creator submitting their own work for review wouldn't reasonably need more than 10 submissions in a single minute — even iterating on drafts, that's generous headroom. 100/day comfortably covers a very active user across a full day of work without giving a scripted flood attack meaningful throughput.

**Evidence (12 rapid requests fired in a loop):**
200
200
200
200
200
200
200
200
200
200
429
429
The first 10 succeeded; requests 11 and 12 were correctly rejected with `429 Too Many Requests`.

## Audit Log

Every submission and appeal writes/updates a structured JSON entry containing: `content_id`, `creator_id`, `timestamp`, `attribution`, `confidence`, `llm_score`, `llm_reasoning`, `stylometric_score`, `status`, and (for appeals) `appeal_reasoning` and `appeal_timestamp`. Retrievable via `GET /log`. Sample entries (from actual testing):

```json
{
  "content_id": "ee71f998-6cf8-47f5-9c87-549c91ddf581",
  "creator_id": "test-ai",
  "timestamp": "2026-07-06T21:16:29.649387+00:00",
  "attribution": "likely_ai",
  "confidence": 0.634,
  "llm_score": 0.8,
  "llm_reasoning": "overly formal tone, repetitive phrase structure, and generic vocabulary suggest AI generation",
  "stylometric_score": 0.386,
  "status": "classified"
},
{
  "content_id": "a2521c37-3f53-4833-9af0-02a5f06656b9",
  "creator_id": "test-human",
  "timestamp": "2026-07-06T21:16:56.372297+00:00",
  "attribution": "likely_human",
  "confidence": 0.216,
  "llm_score": 0.2,
  "llm_reasoning": "informal language and personal experience suggest human authorship, but simplicity and lack of embellishments could indicate AI generation",
  "stylometric_score": 0.239,
  "status": "classified"
},
{
  "content_id": "10d1b876-c695-4efa-9793-3e57ee806075",
  "creator_id": "test-label-check",
  "timestamp": "2026-07-06T21:21:32.046014+00:00",
  "attribution": "likely_human",
  "confidence": 0.185,
  "llm_score": 0.2,
  "stylometric_score": 0.162,
  "status": "under_review",
  "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.",
  "appeal_timestamp": "2026-07-06T21:24:46.285252+00:00"
}
```

## Known Limitations

**Non-native English writing:** Non-native English speakers often rely on a smaller set of "safe" grammatical constructions and more formal, hedged phrasing than native speakers writing casually. Both signals key on exactly these patterns as "AI-like" — the LLM signal because it's trained to associate formality/hedging with AI text, and the stylometric signal because it directly measures sentence-length uniformity, which non-native writing style can produce. This is the system's most concerning false-positive risk, and it's why the "uncertain" band is deliberately wide and the appeals workflow exists.

**Deliberately repetitive creative writing:** A poem or piece using repetition and simple vocabulary as a stylistic device (children's poetry, minimalist prose, incantation-style writing) will likely score high on stylometric uniformity even though the repetition is an intentional artistic choice, not evidence of AI generation. The stylometric signal has no way to distinguish "AI produced this because it defaults to safe patterns" from "a human chose this because repetition is the point."

## Spec Reflection

The spec's requirement to write out the exact label text *before* building anything (Milestone 2) genuinely shaped the implementation — deciding "what should 0.6 confidence communicate to a non-technical reader" before writing code forced the threshold bands to be intentional rather than an afterthought bolted onto whatever the raw scores happened to produce.

Where implementation diverged from the original plan: the wide "uncertain" band (0.35–0.74) ended up catching more inputs than I initially expected — including some that intuitively felt like clearly-AI text (0.634 confidence). Rather than narrowing the band to force more decisive labels, I kept it as designed, since the whole point of the wide band was to bias the system toward caution over false accusations. This surfaced a real tension in the spec: "meaningful variation" between scores doesn't necessarily mean confident binary calls — it can mean confident calls *at the extremes* and appropriate caution everywhere else, which is what this system actually does.
[?1049h[1;32r[1;1H[J[7m  UW PICO 5.09                                    New Buffer                                      [27m[31;1H[K[32;1H[K[31;1H[7m^[27m[7mG[27m Get Help     [7m^[27m[7mO[27m WriteOut     [7m^[27m[7mR[27m Read File    [7m^[27m[7mY[27m Prev Pg      [7m^[27m[7mK[27m Cut Text     [7m^[27m[7mC[27m Cur Pos      [K[32;1H[7m^[27m[7mX[27m Exit         [7m^[27m[7mJ[27m Justify      [7m^[27m[7mW[27m Where is     [7m^[27m[7mV[27m Next Pg      [7m^[27m[7mU[27m UnCut Text   [7m^[27m[7mT[27m To Spell     [K[3;1H[30;1H                                                                                                  [30;44H[7m[ New file ][27m[1;1H[J[7m  UW PICO 5.09                                    File: cat                                       [27m[31;1H[K[32;1H[K[31;1H[7m^[27m[7mG[27m Get Help     [7m^[27m[7mO[27m WriteOut     [7m^[27m[7mR[27m Read File    [7m^[27m[7mY[27m Prev Pg      [7m^[27m[7mK[27m Cut Text     [7m^[27m[7mC[27m Cur Pos      [K[32;1H[7m^[27m[7mX[27m Exit         [7m^[27m[7mJ[27m Justify      [7m^[27m[7mW[27m Where is     [7m^[27m[7mV[27m Next Pg      [7m^[27m[7mU[27m UnCut Text   [7m^[27m[7mT[27m To Spell     [K[3;1H[1;47H[7mFile: cat                                 Modified[27m[3;1H##[3;4HAI[3;7HUsage[31;1H[K[32;1H[K[30;1H                                                                                                  [30;39H[7m[ Can now UnJustify! ][27m[31;1H[7m^[27m[7mG[27m Get Help     [7m^[27m[7mO[27m WriteOut     [7m^[27m[7mR[27m Read File    [7m^[27m[7mY[27m Prev Pg      [7m^[27m[7mK[27m Cut Text     [7m^[27m[7mC[27m Cur Pos      [K[32;1H[7m^[27m[7mX[27m Exit         [7m^[27m[7mJ[27m Justify      [7m^[27m[7mW[27m Where is     [7m^[27m[7mV[27m Next Pg      [7m^[27m[7mU[27m UnJustify    [7m^[27m[7mT[27m To Spell     [K[4;1H[30;1H                                                                                                  [31;1H[K[32;1H[K[31;1H[7m^[27m[7mG[27m Get Help     [7m^[27m[7mO[27m WriteOut     [7m^[27m[7mR[27m Read File    [7m^[27m[7mY[27m Prev Pg      [7m^[27m[7mK[27m Cut Text     [7m^[27m[7mC[27m Cur Pos      [K[32;1H[7m^[27m[7mX[27m Exit         [7m^[27m[7mJ[27m Justify      [7m^[27m[7mW[27m Where is     [7m^[27m[7mV[27m Next Pg      [7m^[27m[7mU[27m UnCut Text   [7m^[27m[7mT[27m To Spell     [K[4;1H1.[4;4H**Flask[4;12Happ[4;16Hskeleton[4;25H+[4;27Hfirst[4;33Hsignal[4;40H(Milestone[4;51H3):**[4;57HI[4;59Hgave[4;64HClaude[4;71Hthe[4;75Hdetection[4;85Hsignals[4;93Hsecti[4;1H$sectio[Kn[4;10Hfrom[4;15H`planning.md`[4;29Hplus[4;34Hthe[4;38Harchitecture[4;51Hdiagram[4;59Hand[4;63Hasked[4;69Hit[4;72Hto[4;75Hgenerate[4;84Hthe[4;88HFlask[4;94Happ[4;98H[4;2H app s[Kkeleton[4;16Hwith[4;21Hthe[4;25H`/submit`[4;35Hroute[4;41Hstub[4;46Hand[4;50Hthe[4;54HGroq[4;59HLLM[4;63Hsignal[4;70Hfunction.[4;80HI[4;82Hreviewed[4;91Hand[4;95Htes[4;2Hd test[Ked[4;11Hthe[4;15Hfunction[4;24Hdirectly[4;33H(calling[4;42Hit[4;45Hwith[4;50Ha[4;52Hfew[4;56Htest[4;61Hstrings)[4;70Hbefore[4;77Hwiring[4;84Hit[4;87Hinto[4;92Hthe[4;96Hen[4;2Hhe end[Kpoint,[4;15Hand[4;19Hcaught[4;26Ha[4;28Hport[4;33Hconflict[4;42H(macOS[4;49HAirPlay[4;57HReceiver[4;66Hhijacking[4;76Hport[4;81H5000)[4;87Hthat[4;92Hrequir[4;2Hequire[Kd[4;10Hmoving[4;17Hthe[4;21Hserver[4;28Hto[4;31Hport[4;36H5001.[3;13H1. **Flask app skeleton + first signal (Milestone 3):** I gave Claude the[4;1Hdetection signals section from `planning.md` plus the architecture diagram and asked it to[5;1Hgenerate the Flask app skeleton with the `/submit` route stub and the Groq LLM signal[6;1Hfunction. I reviewed and tested the function directly (calling it with a few test strings)[7;1Hbefore wiring it into the endpoint, and caught a port conflict (macOS AirPlay Receiver[8;1Hhijacking port 5000) that required moving the server to port 5001.[31;1H[K[32;1H[K[30;1H                                                                                                  [30;39H[7m[ Can now UnJustify! ][27m[31;1H[7m^[27m[7mG[27m Get Help     [7m^[27m[7mO[27m WriteOut     [7m^[27m[7mR[27m Read File    [7m^[27m[7mY[27m Prev Pg      [7m^[27m[7mK[27m Cut Text     [7m^[27m[7mC[27m Cur Pos      [K[32;1H[7m^[27m[7mX[27m Exit         [7m^[27m[7mJ[27m Justify      [7m^[27m[7mW[27m Where is     [7m^[27m[7mV[27m Next Pg      [7m^[27m[7mU[27m UnJustify    [7m^[27m[7mT[27m To Spell     [K[9;1H[30;1H                                                                                                  [31;1H[K[32;1H[K[31;1H[7m^[27m[7mG[27m Get Help     [7m^[27m[7mO[27m WriteOut     [7m^[27m[7mR[27m Read File    [7m^[27m[7mY[27m Prev Pg      [7m^[27m[7mK[27m Cut Text     [7m^[27m[7mC[27m Cur Pos      [K[32;1H[7m^[27m[7mX[27m Exit         [7m^[27m[7mJ[27m Justify      [7m^[27m[7mW[27m Where is     [7m^[27m[7mV[27m Next Pg      [7m^[27m[7mU[27m UnCut Text   [7m^[27m[7mT[27m To Spell     [K[9;1H2.[9;4H**Stylometric[9;18Hsignal[9;25H+[9;27Hconfidence[9;38Hscoring[9;46H(Milestone[9;57H4):**[9;63HI[9;65Hdirected[9;74HClaude[9;81Hto[9;84Hgenerate[9;93Hthe[9;97Hs[9;1H$the st[Kylometric[9;18Hheuristic[9;28Hfunction[9;37Hand[9;41Hthe[9;45Hscore-combination[9;63Hlogic[9;69Hbased[9;75Hon[9;78Hthe[9;82Huncertainty[9;94Hrepr[9;2H repre[Ksentation[9;18Hsection[9;26Hof[9;29H`planning.md`.[9;44HI[9;46Htested[9;53Hthe[9;57Hraw[9;61Hstylometric[9;73Hfunction[9;82Hstandalone[9;93Hagain[9;2Hagains[Kt[9;10Hknown[9;16HAI/human[9;25Htext[9;30Hbefore[9;37Hintegrating[9;49Hit,[9;53Hand[9;57Hverified[9;66Hthe[9;70Hcombined[9;79Hconfidence[9;90Hscores[9;97Ha[9;2Hres ag[Kainst[9;14Hmy[9;17H4[9;19Htest[9;24Hcases[9;30Hmatched[9;38Hthe[9;42Hthreshold[9;52Hbands[9;58Hdefined[9;66Hin[9;69Hthe[9;73Hspec[9;78H—[9;80Hthey[9;85Hdid,[9;90Hrevealin[9;2Healing[K[9;9Hthe[9;13Hcaution-biased[9;28Hbehavior[9;37Hdiscussed[9;47Habove[9;53Hin[9;56Hthe[9;60HSpec[9;65HReflection.[8;68H2. **Stylometric signal +[9;1Hconfidence scoring (Milestone 4):** I directed Claude to generate the stylometric heuristic[10;1Hfunction and the score-combination logic based on the uncertainty representation section of[11;1H`planning.md`. I tested the raw stylometric function standalone against known AI/human text[12;1Hbefore integrating it, and verified the combined confidence scores against my 4 test cases[13;1Hmatched the threshold bands defined in the spec — they did, revealing the caution-biased[14;1Hbehavior discussed above in the Spec Reflection.[31;1H[K[32;1H[K[30;1H                                                                                                  [30;39H[7m[ Can now UnJustify! ][27m[31;1H[7m^[27m[7mG[27m Get Help     [7m^[27m[7mO[27m WriteOut     [7m^[27m[7mR[27m Read File    [7m^[27m[7mY[27m Prev Pg      [7m^[27m[7mK[27m Cut Text     [7m^[27m[7mC[27m Cur Pos      [K[32;1H[7m^[27m[7mX[27m Exit         [7m^[27m[7mJ[27m Justify      [7m^[27m[7mW[27m Where is     [7m^[27m[7mV[27m Next Pg      [7m^[27m[7mU[27m UnJustify    [7m^[27m[7mT[27m To Spell     [K[15;1H[30;1H                                                                                                  [31;1H[K[32;1H[K[31;1H[7m^[27m[7mG[27m Get Help     [7m^[27m[7mO[27m WriteOut     [7m^[27m[7mR[27m Read File    [7m^[27m[7mY[27m Prev Pg      [7m^[27m[7mK[27m Cut Text     [7m^[27m[7mC[27m Cur Pos      [K[32;1H[7m^[27m[7mX[27m Exit         [7m^[27m[7mJ[27m Justify      [7m^[27m[7mW[27m Where is     [7m^[27m[7mV[27m Next Pg      [7m^[27m[7mU[27m UnCut Text   [7m^[27m[7mT[27m To Spell     [K[15;1H3.[15;4H**Label[15;12Hgeneration[15;23H+[15;25Happeals[15;33Hendpoint[15;42H(Milestone[15;53H5):**[15;59HClaude[15;66Hgenerated[15;76Hthe[15;80Hlabel-mapping[15;94Hlogi[15;1H$ logic[K[15;9Hand[15;13Hthe[15;17H`/appeal`[15;27Hendpoint[15;36Hfrom[15;41Hthe[15;45Hlabel[15;51Hvariants[15;60Hand[15;64Happeals[15;72Hworkflow[15;81Hsections[15;90Hof[15;93Hthe[15;97Hs[15;2Hthe sp[Kec.[15;12HI[15;14Hverified[15;23Hall[15;27Hthree[15;33Hlabel[15;39Hvariants[15;48Hwere[15;53Hreachable[15;63Hby[15;66Htesting[15;74Hinputs[15;81Hacross[15;88Hthe[15;92Hconfid[15;2Honfide[Knce[15;12Hrange,[15;19Hand[15;23Hconfirmed[15;33Hthe[15;37Happeal[15;44Hendpoint[15;53Hcorrectly[15;63Hupdated[15;71Hstatus[15;78Hand[15;82Hpreserved[15;92Hthe[15;96Hor[15;2Hhe ori[Kginal[15;14Hclassification[15;29Hdata[15;34Hin[15;37Hthe[15;41Hsame[15;46Hlog[15;50Hentry[15;56Hrather[15;63Hthan[15;68Hoverwriting[15;80Hit.[14;50H3. **Label generation + appeals endpoint[15;1H(Milestone 5):** Claude generated the label-mapping logic and the `/appeal` endpoint from[16;1Hthe label variants and appeals workflow sections of the spec. I verified all three label[17;1Hvariants were reachable by testing inputs across the confidence range, and confirmed the[18;1Happeal endpoint correctly updated status and preserved the original classification data in[19;1Hthe same log entry rather than overwriting it.[31;1H[K[32;1H[K[30;1H                                                                                                  [30;39H[7m[ Can now UnJustify! ][27m[31;1H[7m^[27m[7mG[27m Get Help     [7m^[27m[7mO[27m WriteOut     [7m^[27m[7mR[27m Read File    [7m^[27m[7mY[27m Prev Pg      [7m^[27m[7mK[27m Cut Text     [7m^[27m[7mC[27m Cur Pos      [K[32;1H[7m^[27m[7mX[27m Exit         [7m^[27m[7mJ[27m Justify      [7m^[27m[7mW[27m Where is     [7m^[27m[7mV[27m Next Pg      [7m^[27m[7mU[27m UnJustify    [7m^[27m[7mT[27m To Spell     [K[20;1H[31;1H[K[32;1H[K[?1049l
## AI Usage

1. Flask app skeleton + first signal (Milestone 3): I gave Claude the detection signals section from planning.md plus the architecture diagram and asked it to generate the Flask app skeleton with the /submit route stub and the Groq LLM signal function. I reviewed and tested the function directly (calling it with a few test strings) before wiring it into the endpoint, and caught a port conflict (macOS AirPlay Receiver hijacking port 5000) that required moving the server to port 5001.

2. Stylometric signal + confidence scoring (Milestone 4): I directed Claude to generate the stylometric heuristic function and the score-combination logic based on the uncertainty representation section of planning.md. I tested the raw stylometric function standalone against known AI/human text before integrating it, and verified the combined confidence scores against my 4 test cases matched the threshold bands defined in the spec.

3. Label generation + appeals endpoint (Milestone 5): Claude generated the label-mapping logic and the /appeal endpoint from the label variants and appeals workflow sections of the spec. I verified all three label variants were reachable by testing inputs across the confidence range, and confirmed the appeal endpoint correctly updated status and preserved the original classification data in the same log entry rather than overwriting it.

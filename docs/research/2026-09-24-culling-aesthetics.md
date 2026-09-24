# Culling, aesthetics and selection: research (2026-09-24)

> Owner decisions (2026-09-24): aesthetic head on SigLIP2 embeddings, trained on EVA (CC0 annotations) plus the owner's own ratings; AVA-trained weights not distributed. Rules first; aesthetics only reorders, never deletes.


This container blocks most of the web (huggingface.co, arxiv.org, the vendor sites, CVF and the KonIQ pages), so most pages I could actually fetch are on GitHub or PyPI. A plain number such as [3] means I fetched that page. A number marked **S**, such as [S12], means I only saw the claim in a search result, so it is **unverified**. No model was run here, so every speed and accuracy figure below is unverified for bioscan.

## 1. What matters most

1. **Licences decide more than accuracy does.** Many strong quality (IQA) and aesthetic components are non-commercial or copyleft:
   - pyiqa: PolyForm Noncommercial 1.0.0 [2][3].
   - Q-Align: S-Lab licence, non-commercial [5].
   - CLIP-IQA: NTU S-Lab licence [7].
   - aesthetic-predictor-v2.5: AGPL-3.0 [10].
   - SuperAnimal weights: research use only [20].
   - Depth-Anything-V2 Base, Large and Giant: CC-BY-NC [22].
   - CUB bird images: non-commercial research only [S27].
   - AVA, AADB, TAD66K, PIPAL and SPAQ: I found no licence at all.
   - PARA and FLICKR-AES: research use only [S24][19].

   bioscan is MIT and "open source first", so none of this blocks work today. But mixing these in would make the terms of any bundle unclear, as BirdNET's CC BY-NC-SA already does.
2. **The cheapest high-value step needs no new model.** bioscan already has per-box sharpness and exposure, box geometry, capture time to the sub-second, and whole-frame SigLIP2 embeddings. That is enough for rejects, burst grouping and picking the best frame of a burst.
   - This is also how the wildlife cullers work: they score the subject, not the whole frame.
   - Project Kestrel scores only the bird's own pixels [17].
   - Tack finds eyes and scores sharpness on the subject [S29].
3. **Aesthetics should be a small head on the SigLIP2 embeddings bioscan already computes, trained on the owner's own ratings.** No published head exists for `siglip2-base-patch16-224`:
   - aesthetic-predictor-v2.5 uses a different model, `siglip-so400m-patch14-384` [11].
   - The LAION v1 and v2 predictors use OpenAI CLIP [9][12].

   A generic head trained on AVA or EVA would give a starting point, and the owner's stars would then personalise it.

## 2. Candidate components

Speeds are seconds per ~1080×800 image on CPU (M1 Pro / M2), from pyiqa's own benchmark [4]. I found no published MPS figures. "AVA SRCC" is rank correlation with AVA's aesthetic scores, from pyiqa's aesthetics benchmark [4]. LIVEC (in-the-wild photos) and SPAQ (phone photos) figures come from pyiqa's no-reference quality benchmark [4].

| Component | Measures | Licence (weights / data) | Size and speed | Runs on a Mac | Verdict |
|---|---|---|---|---|---|
| **Box rules** (existing sharpness and exposure, box size, edge contact, placement) | subject focus, exposure, cut-off, too small, thirds | own code (MIT) | about 0 extra | yes | **recommend** |
| **SigLIP2 embedding + trained head** (linear, or a small MLP like LAION v2's 5-layer one [12]) | aesthetics, personal taste, category | backbone Apache-2.0; head licence depends on its training data | microseconds (embedding already computed) | yes | **recommend** |
| **SigLIP2 zero-shot prompts** | scene category | Apache-2.0 | about 0 extra | yes | **recommend** (measure first) |
| LAION aesthetic v1 (linear) / v2 (MLP) | "how much people like it" | v1 MIT [9]; v2 Apache-2.0, trained on SAC + LAION-Logos + AVA [1][12][S13] | 0.25 / 0.40 s; AVA SRCC 0.665 [4] | yes, but needs CLIP ViT-L/14 as a second backbone | consider (reference only) |
| aesthetic-predictor-v2.5 | aesthetics, including illustrations | AGPL-3.0 [10]; training data not disclosed [11] | SigLIP so400m (~400M params) | yes | avoid (AGPL, different backbone) |
| NIMA (idealo, MobileNet) | aesthetics (AVA), technical quality (TID2013) | Apache-2.0 code and weights [14] | 0.08–0.15 s; AVA SRCC 0.66–0.71 [4] | yes | consider (weak baseline) |
| TOPIQ-IAA | aesthetics | pyiqa, non-commercial | 0.31–0.37 s; AVA SRCC 0.791 [4] | yes | consider (benchmarking only) |
| Q-Align / OneAlign | quality and aesthetics as rating words from a large model (mPLUG-Owl2) | S-Lab, non-commercial [5]; trained on KonIQ, SPAQ, KADID, AVA [6] | ~15 GB GPU memory; AVA SRCC 0.822, LIVEC 0.881 [4] | heavy | avoid (licence, size) |
| VILA (VILA-R) | aesthetics learned from AVA comments | Apache-2.0 code; repo datasets CC BY 4.0 [15][16]; checkpoint licence not stated | TF Hub | TensorFlow only | consider (reference) |
| MUSIQ | technical quality | Apache-2.0 code; KonIQ/SPAQ/PaQ2PiQ/AVA checkpoints [8] | 0.73–0.84 s; LIVEC 0.789 [4] | yes | consider |
| MANIQA | technical quality | Apache-2.0 [18]; trained on PIPAL, KADID, KonIQ | 8.6–10.5 s [4] | slow | avoid |
| TOPIQ-NR | technical quality | pyiqa, non-commercial | 0.84–1.0 s; LIVEC 0.811, SPAQ 0.870 [4] | yes | consider |
| CLIP-IQA / CLIP-IQA+ | quality plus "abstract" attributes | NTU S-Lab [7] | 0.55–0.80 s [4] | yes | consider (the zero-shot idea only) |
| LIQE | quality, scene and distortion type | MIT code [21] | 0.19–0.35 s [4] | yes | consider |
| DBCNN | technical quality | pyiqa | 1.4–1.8 s; LIVEC 0.756 [4] | yes | avoid |
| BRISQUE / NIQE | classical quality measures, no weights | none needed | 0.13 / 0.22 s; LIVEC 0.31 / 0.45 [4] | yes | avoid (too weak) |
| SAMP-Net + CADB | composition score, 13 composition patterns | MIT repo; 9,497 AADB images, 5 raters each [23] | ~180 MB | yes | consider (for evaluation) |
| U²-Net / u2netp | saliency mask (clutter) | Apache-2.0 [24] | 176 MB / 4.7 MB | yes | consider for v1 |
| Depth-Anything-V2 Small | depth, for subject–background separation | Apache-2.0 (Small only) [22] | 24.8M params | yes | consider for v1 |
| AP-10K keypoints | 17 keypoints including both eyes, 54 species | CC-BY-4.0 data [25] | mmpose | yes | consider (mammal eyes) |
| SuperAnimal | quadruped pose | research-only weights [20] | — | yes | avoid |
| Places365 CNNs | 365 scene classes | models CC BY; dataset non-commercial [26] | ResNet | yes | avoid (SigLIP2 is already loaded) |
| **Qwen3-VL 2B/4B/8B** via mlx-vlm | scene tags, composition critique, choosing between two frames | Apache-2.0 [27]; mlx-vlm MIT [28] | 2–8B params; speed not measured | yes (MLX) | **consider, shortlist only** |
| Qwen2.5-VL | same | 7B Apache-2.0; 3B and 72B under the Qwen licence [S30] | — | yes | consider the 7B only |
| InternVL3.5 | same | MIT code; language-model parts keep the Qwen/InternLM terms [29] | 1B–241B | yes | consider |
| MiniCPM-V 4.6 | same | repo Apache-2.0 [31]; weight terms not confirmed | 1.3B | yes | consider |

Dataset licences:
- KonIQ-10k metadata: CC0 [S32].
- EVA: CC0, 4,070 images taken from AVA [33]; the copyright of the images themselves is unclear.
- KADID: source images under the Pixabay licence [S34].
- AVA: images from dpchallenge.com, no licence found [S35].
- AADB, TAD66K, SPAQ, PIPAL: no licence found [36][37][S38].
- PARA: academic research only [S24].
- FLICKR-AES: Creative Commons images, "research purpose only" [19].
- HLW (horizon lines): access on request, licence not found [S39].

## 3. Findings by topic

**Technical quality.** Generic quality models score the whole frame. In wildlife shots that frame is often a sharp background behind a soft bird, which is exactly the case bioscan's crop-based `quality` already handles. These models also transfer poorly between datasets: BRISQUE reaches only SRCC 0.31 on LIVEC [4]. Treat them as bench experiments, not defaults. If one is tried, run MUSIQ-KonIQ or TOPIQ-NR on the subject crop and compare it with the current sharpness score using the harness.

**Aesthetics.** On AVA, the best scores come from the heaviest and most restrictively licensed models: Q-Align reaches 0.82 against 0.67 for the LAION MLP [4]. A judge built on Qwen2.5-VL-7B reportedly reaches SRCC ~0.73 zero-shot across five aesthetic benchmarks [S40]. Two cautions:
- AVA is contest photos rated by dpchallenge voters, not one wildlife photographer's taste.
- An audit of the LAION predictor questions whose taste it encodes [S41].

**Composition.** Some checks are cheap from boxes alone:
- Subject too small: box area divided by frame area.
- Cut-off: a box edge within about 1% of the frame edge, unless the box is large, which suggests a deliberate tight crop.
- Placement: distance from the box centre to the thirds points or to the centre.
- Headroom: the gap above the box.

Others need more than boxes:
- Lead room needs the direction the animal faces. The cheapest option (unverified) is a second OWLv2 query for "head" or "eye" inside the box, since OWLv2 is already loaded.
- Horizon tilt needs line detection on the frame. For v0, finding the dominant near-horizontal line (Hough or LSD) is enough, applied only to landscape and water scenes.
- Clutter and subject–background separation need saliency (u2netp [24]) or depth (Depth-Anything-V2-Small [22]).

CADB with SAMP-Net scores composition along 13 pattern types [23]. It is useful for evaluation, but it is built on AADB images, whose licence is unknown.

**Wildlife-specific.** Eye-in-focus is what the wildlife cullers sell:
- Tack detects eyes and scores subject sharpness, locally [S29].
- Project Kestrel (AGPLv3) combines several parts [17]: SpeciesNet/MegaDetector for detection, SAM-HQ masks, a custom quality model on the bird's pixels only, and grouping of frames by scene similarity.
- **Lightroom Classic's Assisted Culling is real.** Adobe has help pages for it [S42]. It has sliders for subject focus, eye focus and eyes open, and the June 2026 update (v15.4) added a Faces panel and duplicate detection [S43].
- Aftershoot flags blurry, closed-eye and duplicate frames, with a lenient mode for wildlife bursts [S44].
- Narrative Select and FilterPixel are built around faces and portraits; Excire is more of an organiser that groups similar frames [S45].

I found no licence-clean bird-eye model. CUB bird keypoints are non-commercial [S27]. AP-10K has eye keypoints for mammals and other animals under CC-BY-4.0 [25]. On catchlight I found nothing; at most it is a heuristic, such as a bright spot inside the eye box (unverified).

**Bursts and duplicates.** The usual method is cosine similarity between image embeddings with a threshold near 0.93–0.95 [S46]. For bioscan:
1. Split frames by camera and by sub-second gaps in `taken_at`, which bioscan already records. The repo's strategy doc proposes the same (`docs/strategy/2026-09-24-opportunities.md:186`).
2. Within each time window, link frames whose SigLIP2 cosine is above the threshold.
3. Pick the best frame of each burst by these criteria, in order: subject sharpness (eye box if available, else the subject box), no cut-off, exposure within limits, then the aesthetic score.

**Scene categories.** SigLIP2 zero-shot prompts cost nothing, because the embeddings and the text model are already loaded. A Hugging Face forum thread reports that SigLIP2 zero-shot accuracy comes out lower than published [S47], so measure it on your photos first. Places365 CNNs are CC BY, but the dataset is non-commercial [26]. A vision-language model (VLM) tagger is the most flexible (for example "street" versus "people") but the slowest.

**Personalisation.** The research reports few-shot personal models at 10 and 100 rated images per user on FLICKR-AES, averaged over 37 test users [S48]. FLICKR-AES is Creative Commons images under a research-only licence [19]. PARA has 31,220 images and 438 raters, about 25 raters per image [S24]. I found no primary figure for how many of one person's ratings a linear head on frozen features needs. The harness should measure a learning curve at 50, 100, 200, 500 and 1,000 of the owner's own ratings.

## 4. Recommended v0 stack

| Goal step | v0 mechanism | New model? |
|---|---|---|
| 1. Rejects | On the best box: sharpness below a limit (focus or motion blur), exposure off by more than a limit plus a share of clipped pixels (new, cheap), box touching the frame edge (cut-off), box area below a limit (too small). Horizon tilt from line detection, landscape category only. Clutter waits for v1. | no |
| 1b. Duplicates | Burst = same camera with gaps under about 1 s, then SigLIP2 cosine at or above a threshold tuned on bench | no |
| 2. Best frames | Keep the best frame of each burst, then rank by the **SigLIP2 aesthetic head**. First a generic head trained on EVA (CC0 annotations) or AVA, not distributed until its licence is clear; then refit on the owner's stars. | **1: the head (a few kB)** |
| 3. Per category | SigLIP2 zero-shot prompts (landscape, people/street, wildlife from the existing gate, macro, architecture, …); top x per category by aesthetic score, skipping near-duplicates | no |
| 4. Photo book | Same ranking plus variety across time and place; optionally a **Qwen3-VL-4B or 8B (Apache-2.0, MLX)** pass over a ~200-frame shortlist to check categories and break ties between two frames | **2: optional VLM** |

Why this approach, in three sentences. Rules on the subject crop already explain most wildlife rejects, cost nothing, and can be read and tuned one by one. One head on the embeddings bioscan already computes adds aesthetics without a second backbone or a non-commercial library. A VLM is too slow for thousands of frames but affordable on a shortlist, and the Qwen3-VL models are Apache-2.0.

## 5. Evaluation plan with the harness

**Ground truth (a new `cull` tier).**
- Read the owner's Lightroom stars and colour labels from XMP (sidecars or embedded) with `exiftool`, which `gt folders` already uses.
- As far as I know, Lightroom Classic's pick/reject flags are **not written to XMP** (unverified). They would need a catalog export or a mapping such as "reject = 1 star".
- A new ground-truth CSV would add columns such as `rating`, `pick`, `reject_reason`, `burst_id` and `category`. It is not committed data until you approve it.
- One labelling pass over 2–3 trips (3,000+ frames): the owner marks each reject with a reason, picks one winner per burst, and sets a category. The burst winners give clean labels for best-of-burst.

**Metrics.** Each comes with Wilson intervals, as bench already reports.
- **Keepers lost:** the share of frames the owner picked that the pipeline would auto-reject. This is the budget metric, with only a very small rise allowed.
- Reject precision and recall, per reject reason.
- Burst grouping: pairwise F1 against `burst_id`, and how often the pipeline's burst winner matches the owner's.
- Selection per trip: precision@k and recall@k against the picks (k = the owner's number of picks), plus NDCG@k against stars.
- Ranking: Spearman and Kendall τ between the aesthetic score and stars, per category.
- Category accuracy and confusion matrix.
- Personalisation learning curve: SRCC against the number of ratings. Split by trip, never by frame, so frames from one burst cannot sit on both sides.
- Throughput: extra ms per image.

**How it runs in bench.**
- Add a `cull` scope to the report schema.
- Add budgets for keepers lost and precision@k to `baselines/budget.toml`.
- Commit a `cull-own-<date>` baseline.
- Run `bench compare`, with McNemar on per-image pick/reject flips.
- Optional sanity checks against public sets, for evaluation only and never distributed: AVA and KonIQ/SPAQ (SRCC), CADB (composition).

## 6. Risks

- **Licences.** Adopting pyiqa, Q-Align, CLIP-IQA, SuperAnimal, the larger Depth-Anything-V2 models, AGPL predictors or AVA-trained weights mixes non-commercial or copyleft terms into an MIT project. A head trained on AVA inherits AVA's unclear status. Train on the owner's own ratings or the CC0 EVA annotations, and record where each head came from in `result.engine.models` and in the settings fingerprint.
- **Subjectivity.** A generic aesthetic score encodes the taste of contest voters or LAION raters [S41]. Report agreement with the owner, never "aesthetic accuracy". Keep rejects rule-based and explainable; aesthetics should only reorder frames, never delete them.
- **Uneven cost of mistakes.** Losing a keeper costs far more than reviewing one extra frame. Keep auto-reject conservative and separate from ranking.
- **Leakage and small numbers.** Bursts inflate correlations, so split by trip or burst. One photographer's stars are few and noisy.
- **Domain shift.** Quality models are trained on phone and web photos, and telephoto bokeh can read as blur. Always score the subject crop.
- **Unmeasured claims.** No speed or accuracy figure here comes from bioscan or from a Mac with MPS. All stay unverified until your Mac or CI `models.yml` runs them.

## Sources

1. https://github.com/christophschuhmann/improved-aesthetic-predictor
2. https://github.com/chaofengc/IQA-PyTorch
3. https://raw.githubusercontent.com/chaofengc/IQA-PyTorch/main/LICENSE ; https://pypi.org/pypi/pyiqa/json
4. https://raw.githubusercontent.com/chaofengc/IQA-PyTorch/main/tests/Efficiency_benchmark.csv ; …/tests/IAA_benchmark_results.csv ; …/tests/NR_benchmark_results.csv
5. https://raw.githubusercontent.com/Q-Future/Q-Align/main/LICENSE
6. https://github.com/Q-Future/Q-Align
7. https://github.com/IceClear/CLIP-IQA
8. https://github.com/google-research/google-research/tree/master/musiq
9. https://github.com/LAION-AI/aesthetic-predictor
10. https://pypi.org/pypi/aesthetic-predictor-v2-5/json
11. https://github.com/discus0434/aesthetic-predictor-v2-5 (source file `siglip_v2_5.py`)
12. https://github.com/christophschuhmann/improved-aesthetic-predictor/blob/main/simple_inference.py
13. S: https://arxiv.org/pdf/2601.09896 (SAC = Simulacra Aesthetic Captions)
14. https://github.com/idealo/image-quality-assessment
15. https://github.com/google-research/google-research/tree/master/vila
16. https://github.com/google-research/google-research
17. https://github.com/SanjaySoniLV/ProjectKestrel
18. https://github.com/IIGROUP/MANIQA
19. https://github.com/alanspike/personalizedImageAesthetics
20. https://github.com/DeepLabCut/DeepLabCut
21. https://github.com/zwx8981/LIQE
22. https://github.com/DepthAnything/Depth-Anything-V2
23. https://github.com/bcmi/Image-Composition-Assessment-Dataset-CADB
24. https://github.com/xuebinqin/U-2-Net ; S (PARA): https://arxiv.org/abs/2203.16754 and https://github.com/lwchen6309/aesthetics_transfer_learning
25. https://github.com/AlexTheBad/AP-10K
26. https://github.com/CSAILVision/places365
27. https://github.com/QwenLM/Qwen3-VL
28. https://github.com/Blaizzy/mlx-vlm
29. https://github.com/OpenGVLab/InternVL
30. S: https://huggingface.co/Qwen/Qwen2.5-VL-72B-Instruct/blob/main/LICENSE ; https://github.com/QwenLM/Qwen3-VL/issues/963
31. https://github.com/OpenBMB/MiniCPM-V
32. S: https://database.mmsp-kn.de/koniq-10k-database.html
33. https://github.com/kang-gnak/eva-dataset
34. S: https://database.mmsp-kn.de/kadid-10k-database.html
35. S: https://github.com/imfing/ava_downloader
36. https://github.com/woshidandan/TANet-image-aesthetics-and-quality-assessment (TAD66K)
37. https://github.com/lllllllllllll-llll/SPAQ ; https://github.com/HaomingCai/PIPAL-dataset
38. S: https://ics.uci.edu/~skong2/aesthetics.html (AADB)
39. S: http://hlw.csr.uky.edu/
40. S: https://arxiv.org/html/2606.05778
41. S: https://arxiv.org/pdf/2601.09896
42. S: https://helpx.adobe.com/lightroom-classic/help/assisted-culling.html
43. S: https://www.dpreview.com/news/9872416768/adobe-lightroom-photoshop-update-june-2026/ ; https://petapixel.com/2025/11/03/lightrooms-new-features-ai-culling-auto-dust-removal-color-variance-slider-and-more/
44. S: https://aftershoot.com/culling-faq/
45. S: https://filterpixel.com/filterpixel-vs-narrative-select ; https://www.shutternoise.com/articles/ai-photo-culling-compared-2026.html
46. S: https://msightflow.ai/blog/image-deduplication ; https://arxiv.org/html/2312.07273v2
47. S: https://discuss.huggingface.co/t/siglip-2-models-show-lower-zero-shot-accuracy-than-reported/166735
48. S: https://www.ecva.net/papers/eccv_2024/papers_ECCV/papers/05680.pdf

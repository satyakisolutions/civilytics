# Satyaki Solutions — Solution Inventory

Repository scanned: `/home/subhadra/Projects/Satyaki/LSG-Wastewatch`
Scope: one productized solution ships from this repo — **WasteWatch**. All facts below cite `file:line`. Anything not sourced is labeled **NUMBERS GAP** or **[INDUSTRY BENCHMARK]**.

---

## SOLUTION: WasteWatch

**One-line definition.** Real-time AI surveillance platform that turns existing RTSP IP cameras into an evidence-grade illegal-waste-dumping detection, alerting, and license-plate-capture system on a single GPU node.
Source: `docs/executive-summary.md:3`, `docs/executive-summary.md:13`.

### Industries
- Local Self-Government / municipal sanitation (LSG bodies, corporations, panchayats). Source: `docs/business-impact.md:9`.
- Residential communities / RWAs (e.g., Prestige, Sobha). Source: `docs/cost-breakdown.md:414`.
- Industrial estates / tech parks (Peenya, Electronic City, Manyata, RMZ, Embassy). Source: `docs/cost-breakdown.md:416,419`.
- Smart City SPVs (Bengaluru Smart City Ltd). Source: `docs/cost-breakdown.md:417`.
- Waste management operators (Hasiru Dala, BWSSB transfer stations). Source: `docs/cost-breakdown.md:418`.

### Problem removed
- Enforcement is reactive: complaints arrive hours/days after dumping; offenders gone; evidence circumstantial. Source: `docs/executive-summary.md:9`.
- Sanitation teams spend **60–70% of time cleaning dumps** rather than preventing them. Source: `docs/executive-summary.md:9`.
- **Zero prosecutions** in most jurisdictions due to no timestamped attributable evidence. Source: `docs/business-impact.md:13`.
- Manual CCTV review misses **>80%** of incidents (reviewer fatigue → <20% detection). Source: `docs/business-impact.md:21`.

### Tech stack
- **GPU pipeline**: NVIDIA DeepStream 6.x + RF-DETR (TensorRT FP16, 640×640, batch 4) + ByteTrack MOT + nvdsanalytics ROI. Source: `docs/technical-approach.md:34-66`.
- **VLM**: Qwen2.5-VL-7B-Instruct-AWQ (4-bit) served by vLLM, ~7 GB VRAM. Source: `docs/technical-approach.md:95-96,326`.
- **Video**: MediaMTX RTSP↔WebRTC (WHEP) bidirectional. Source: `docs/technical-approach.md:22-27`.
- **Events**: Apache Kafka, 9 topics, 8 async worker consumers. Source: `docs/executive-summary.md:45`, `docs/technical-approach.md:199-208`.
- **Backend**: FastAPI (async), 15+ REST + 3 WebSocket channels. Source: `docs/executive-summary.md:46`, `docs/technical-approach.md:170`.
- **Frontend**: React 18 + TypeScript + Vite + Tailwind + Shadcn/ui + Leaflet + Recharts. Source: `docs/technical-approach.md:246,251`.
- **Data**: PostgreSQL (Neon serverless, 12 tables, 5 Alembic migrations) + Redis + Qdrant (`all-MiniLM-L6-v2`, 384-dim). Source: `docs/technical-approach.md:213-224`.
- **Auth**: Keycloak OAuth2/OIDC + JWT + RBAC (admin/operator/viewer). Source: `docs/technical-approach.md:189-192`.
- **Alerts**: Telegram bot + WebSocket + Piper TTS (`en_US-lessac-medium`) + SIP/Twilio. Source: `docs/technical-approach.md:124-126,160-161`, `docs/cost-breakdown.md:300`.
- **Evidence**: Smart snapshot (confidence 40% / sharpness 30% / framing 30%) + Dropbox sync w/ 3-retry backoff. Source: `docs/technical-approach.md:132-149`.
- **LPR**: PaddleOCR w/ Indian plate regex. Source: `docs/technical-approach.md:153-155`.
- **Deployment**: Docker Compose, 9 services, single-node. Source: `docs/executive-summary.md:50`, `docs/technical-approach.md:310-320`.

### Hard numbers (every one cited)

Detection / product spec
- Detection latency **15–30 s** vs manual review hours-to-days. Source: `docs/executive-summary.md:33`.
- Dwell threshold default **15 s** to trigger VLM. Source: `docs/executive-summary.md:19`, `docs/technical-approach.md:80`.
- Pipeline runs at **30 FPS**, frame skip 2. Source: `docs/executive-summary.md:17`, `docs/technical-approach.md:63`.
- Alert cooldown **60 s/camera**, rate limit **10/hour/camera** (defaults). Source: `docs/technical-approach.md:121-122`.
- Lost-track timeout **30 s**; bbox history sampled ~0.5 s, cap 50 frames. Source: `docs/technical-approach.md:67-69`.
- VLM worker concurrency cap **2**. Source: `docs/technical-approach.md:100`.
- Alert delivery target **<2 s** from VLM result; frontend video **<500 ms**; API p95 **<50 ms**. Source: `docs/technical-approach.md:397-399`.

Codebase scale
- **50 commits | 131 files | 6,359 LOC | 30 automated tests**. Source: `docs/executive-summary.md:73`.

Hardware / capacity
- Reference build: Intel i5-12400F, 16 GB DDR4, RTX 3060 12 GB, 124 GB SSD, Ubuntu 24.04.4, CUDA 12.x. Source: `docs/cost-breakdown.md:284-289`.
- 4-camera VRAM budget **~9.6 / 12 GB @ 30 FPS** ("sweet spot"). Source: `docs/cost-breakdown.md:310`.
- 6-cam ~11 GB, 8-cam OOM on 12 GB. Source: `docs/cost-breakdown.md:311-312`.
- Recommended RTX 3090 tier: 24 GB VRAM, 32 GB RAM, 500 GB SSD, hardware under **$2,000**. Source: `docs/executive-summary.md:13,56-60`.

AWS unit economics (measured against AWS ap-south-1 pricing April 2026)
- 4-cam Reserved **$1,375/mo** ($344/cam/mo); 8-cam **$1,881**; 16-cam **$2,916** ($182/cam/mo). Source: `docs/cost-breakdown.md:15-17,218-222`.
- GPU compute = **~44%** of 4-cam bill. Source: `docs/cost-breakdown.md:19`.
- 3-Yr All-Upfront RI cuts GPU cost **61%** ($370 vs $958). Source: `docs/cost-breakdown.md:116`.
- Spot VLM saves **~$327/mo (54%)**. Source: `docs/cost-breakdown.md:125`.

On-prem OPEX (Lake Shore Gardens reference site)
- **₹5,540 / $66 per month** total OPEX (power ₹1,440, internet ₹2,500, SIP ₹500, cloud sync ₹800, SSL ₹100, UPS ₹200). Source: `docs/cost-breakdown.md:293-303`.
- Server one-time **₹75,000 ($900)**. Source: `docs/cost-breakdown.md:283-289,318`.
- 3-yr TCO on-prem **₹2,74,440 ($3,276)** vs AWS **₹41,40,000 ($49,500)** → **10–15× cheaper on-prem** at ≤4 cams. Source: `docs/cost-breakdown.md:320-322,328`.

Commercial pricing (INR, Tier 1 Bangalore)
- Starter: setup ₹2,00,000; hardware ₹2,50,000; sub ₹25,000/mo; annual ₹2,70,000; addl cam ₹5,000/mo. Y1 total **₹7,20,000**. Source: `docs/cost-breakdown.md:354-361`.
- Professional: setup ₹5,00,000; hardware ₹5,00,000; sub ₹50,000/mo; Y1 **₹15,40,000**. Source: `docs/cost-breakdown.md:367-374`.
- Enterprise: setup ₹15,00,000; sub ₹1,50,000/mo; Y1 **₹35,00,000+**. Source: `docs/cost-breakdown.md:379-387`.
- Blended Y1 margin **56%**, Y2+ **78%**. Source: `docs/cost-breakdown.md:397-398`.

Business-impact projections (Kerala LSG scenario — model, not measured)
- Kerala scale: **14 districts, 1,200+ LSG bodies**. Source: `docs/business-impact.md:9`.
- Complaints **50–100/mo/municipality**, actual **3–5×**. Cleanup **₹5k–₹50k/site**. Annual cleanup **₹30–60 lakh/muni**. Source: `docs/business-impact.md:10-13`.
- Cost-per-camera positioning: WasteWatch **₹2–4K/cam/mo** vs manual **₹15–25K/guard**, CCTV+review **₹5–10K/cam**. Source: `docs/business-impact.md:19-23`.
- 10-cam pilot projection: annual savings **₹53 lakh**, WasteWatch cost **₹8 lakh**, net **₹45 lakh**, **ROI 562%**. Source: `docs/business-impact.md:31-41`.
- District 50-cam scenario: TCO **₹32.5 lakh/yr**, savings **₹2.65 crore/yr**, **ROI 815%**. Source: `docs/business-impact.md:110-120`.
- Fine-revenue projection: **50 violations/mo × ₹5–25k → ₹30–75 lakh/yr per municipality**. Source: `docs/business-impact.md:46-51`.

[INDUSTRY BENCHMARK] claims (not our measurement)
- **40–60%** violation drop within 3 months of visible AI surveillance; **70–80%** cut in repeats when fines enforced. Source: `docs/business-impact.md:57-59`.
- Cleaner-neighborhood premium **10–15%** property values. Source: `docs/business-impact.md:215`.

Customer-pitch ROI table (illustrative, not measured)
- Detection time "hours → <5 s", guards 3→1, coverage 8h→24/7. Source: `docs/cost-breakdown.md:518-528`.
- Payback 3–6 months, monthly net ₹1,15,000. Source: `docs/cost-breakdown.md:441,483`.

### Named clients / deployments
- **Lake Shore Gardens (LSG), Thindlu, Vidyaranyapura, Bangalore** — Neeraj's server, first production site, RTX 3060 build. Source: `README.md:26,34`; `docs/cost-breakdown.md:276-278`.
- Government of Kerala LSG Department — target/prospect (docs are prepared for them, not signed). Source: `docs/executive-summary.md:85`, `docs/business-impact.md:219`.
- No other production customer names appear in the repo. **NUMBERS GAP: number of paying customers, active cameras in production, actual measured violations detected, actual fines collected.**

### NUMBERS GAP (unsourced or claimed-but-not-measured)
- Detection accuracy / precision / recall for RF-DETR on Kerala data — no benchmark file.
- VLM `is_dumping` accuracy, false-positive rate — targets stated (`<20%` → `<10%`, `docs/business-impact.md:196`) but no measurement.
- Real-world alert-to-response time from any live deployment.
- Actual `%` uptime at LSG Bangalore site.
- Actual violations caught / fines issued at any customer site.
- All ROI, deterrence %, cleanup-cost-reduction figures in `docs/business-impact.md` are **projections**, not results.
- All INR pricing margins assume claimed OPEX; no invoice-verified data.

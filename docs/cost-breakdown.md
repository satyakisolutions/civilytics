# Civilytics — AWS Deployment Cost Breakdown

**Satyaki Solutions Pvt Ltd** | https://satyaki.co.in
**Document Version**: 1.0 | **Date**: April 2026
**Region**: ap-south-1 (Mumbai, India)

---

## Executive Summary

Civilytics is deployed as a microservices architecture on AWS, leveraging GPU instances for real-time video AI (DeepStream + VLM) and Fargate for CPU-bound services. This document details the cost structure across three deployment tiers.

| Tier | Cameras | Monthly (Reserved) | Monthly (On-Demand) | Annual (Reserved) |
|:----:|:-------:|-------------------:|--------------------:|------------------:|
| **Small** | 4 | **$1,375** | $1,726 | **$16,500** |
| **Medium** | 8 | **$1,881** | $2,449 | **$22,572** |
| **Large** | 16 | **$2,916** | $3,827 | **$34,992** |

**Key cost driver**: GPU compute accounts for ~44% of total cost in the 4-camera tier. Reserved Instance pricing (1-year commitment) saves 20% on the GPU instance, which is the single highest-cost line item.

---

## Detailed Cost Breakdown — 4 Camera Deployment

| Category | AWS Service | Configuration | On-Demand $/mo | Reserved $/mo | Notes |
|----------|-------------|---------------|---------------:|--------------:|-------|
| **Compute — GPU** | EC2 g5.2xlarge | 8 vCPU, 32GB RAM, NVIDIA A10G 24GB | $958 | $607 | Pipeline (DeepStream) + VLM (vLLM Qwen2.5-VL-7B-AWQ), 24/7 operation |
| **Compute — CPU** | ECS Fargate | 11 tasks, ~5.75 vCPU, ~11GB total | $165 | $165 | No reserved pricing for Fargate Compute Savings Plans available |
| **Messaging** | Amazon MSK | 2× kafka.m5.large brokers | $380 | $380 | 12 topics, 7-day retention, ~50MB/s throughput |
| **Cache** | Amazon ElastiCache | cache.t3.medium (Redis 7) | $47 | $47 | Command channel (pub/sub), rate-limit throttling, session caching |
| **Database** | Amazon RDS PostgreSQL | db.t3.medium, Multi-AZ | $98 | $98 | 100GB gp3 storage, automated daily backups, 7-day retention |
| **Storage** | Amazon S3 | ~100GB/mo evidence | $3 | $3 | S3 Standard + lifecycle to IA (30d) + Glacier (90d) |
| **Networking** | ALB | 1 ALB, ~500GB data transfer | $25 | $25 | HTTPS :443 + WebSocket + WebRTC :8889 |
| **Networking** | NAT Gateway | ~100GB egress from private subnets | $35 | $35 | Required for Fargate + EC2 internet access |
| **Container Registry** | Amazon ECR | ~5GB container images | $1 | $1 | 15 service images, lifecycle policy to prune old tags |
| **Secrets** | AWS Secrets Manager | ~10 secrets | $4 | $4 | Keycloak admin, Telegram bot token, Twilio SID, DB creds, etc. |
| **Monitoring** | Amazon CloudWatch | Basic metrics + log groups | $10 | $10 | Custom metrics for pipeline FPS, VLM latency, queue depth |
| | | **TOTAL** | **$1,726** | **$1,375** | **Savings: $351/mo (20%)** |

### Fargate Task Breakdown (11 tasks)

| Task | vCPU | Memory | Estimated $/mo | Role |
|------|-----:|-------:|---------------:|------|
| api | 0.5 | 1 GB | $18.40 | FastAPI gateway, REST + WebSocket |
| keycloak | 1.0 | 2 GB | $36.79 | Identity & access management (SSO) |
| vlm-worker | 0.5 | 1 GB | $18.40 | VLM request orchestrator (Kafka consumer) |
| tts-worker | 0.5 | 1 GB | $18.40 | Text-to-speech synthesis |
| lpr-worker | 1.0 | 2 GB | $36.79 | License plate recognition (OCR) |
| alert-svc | 0.25 | 512 MB | $9.20 | Alert dispatch (Telegram, email) |
| sip-svc | 0.25 | 512 MB | $9.20 | SIP/voice call management |
| evidence-svc | 0.5 | 1 GB | $18.40 | Evidence packaging → S3 |
| log-worker | 0.25 | 512 MB | $9.20 | Log aggregation and shipping |
| mediamtx | 0.5 | 512 MB | $13.80 | RTSP→WebRTC media gateway |
| qdrant | 0.5 | 1 GB | $18.40 | Vector similarity search DB |
| **Total** | **5.75** | **11 GB** | **~$207** | *Billed: ~$165 with Compute Savings Plan* |

> **Note**: Fargate pricing in ap-south-1 is $0.04656/vCPU/hr + $0.00511/GB/hr. Compute Savings Plans reduce this by ~20%.

---

## 8-Camera Deployment

Scaling from 4 to 8 cameras requires a larger GPU instance and additional worker replicas.

| Category | AWS Service | Configuration | On-Demand $/mo | Reserved $/mo | Notes |
|----------|-------------|---------------|---------------:|--------------:|-------|
| **Compute — GPU** | EC2 g5.4xlarge | 16 vCPU, 64GB RAM, NVIDIA A10G 24GB | $1,916 | $1,214 | Handles 8 concurrent RTSP streams |
| **Compute — CPU** | ECS Fargate | 15 tasks, ~9 vCPU, ~17GB | $265 | $265 | Scale: lpr-worker ×2, vlm-worker ×2, tts-worker ×2 |
| **Messaging** | Amazon MSK | 3× kafka.m5.large brokers | $570 | $570 | Additional broker for throughput headroom |
| **Cache** | Amazon ElastiCache | cache.t3.medium (Redis 7) | $47 | $47 | Same config, handles 8-cam load |
| **Database** | Amazon RDS PostgreSQL | db.t3.large, Multi-AZ | $147 | $147 | 200GB gp3, ~2× event throughput |
| **Storage** | Amazon S3 | ~200GB/mo evidence | $5 | $5 | 2× evidence volume |
| **Networking** | ALB | 1 ALB, ~1TB data transfer | $30 | $30 | Increased data transfer |
| **Networking** | NAT Gateway | ~200GB egress | $45 | $45 | Increased egress |
| **Container Registry** | Amazon ECR | ~5GB | $1 | $1 | Same |
| **Secrets** | AWS Secrets Manager | ~10 secrets | $4 | $4 | Same |
| **Monitoring** | Amazon CloudWatch | Metrics + logs (8 cams) | $15 | $15 | More log volume |
| | | **TOTAL** | **$3,045** | **$2,343** | **Savings: $702/mo (23%)** |

> **Note**: g5.4xlarge provides the same single A10G GPU but with 2× CPU/RAM. Both pipeline and VLM benefit from additional CPU cores for pre/post-processing. At 8 cameras, VRAM usage is ~20GB of 24GB.

---

## 16-Camera Deployment

16 cameras require dual GPU instances (2× g5.2xlarge) and significant worker scaling.

| Category | AWS Service | Configuration | On-Demand $/mo | Reserved $/mo | Notes |
|----------|-------------|---------------|---------------:|--------------:|-------|
| **Compute — GPU** | 2× EC2 g5.2xlarge | 2× (8 vCPU, 32GB, A10G 24GB) | $1,916 | $1,214 | 8 cameras per instance |
| **Compute — CPU** | ECS Fargate | 22 tasks, ~14 vCPU, ~26GB | $420 | $420 | All workers ×2-3, api ×2 |
| **Messaging** | Amazon MSK | 3× kafka.m5.xlarge brokers | $760 | $760 | Larger brokers for 4× throughput |
| **Cache** | Amazon ElastiCache | cache.m5.large (Redis 7) | $94 | $94 | Upgraded for higher pub/sub volume |
| **Database** | Amazon RDS PostgreSQL | db.r6g.large, Multi-AZ | $196 | $196 | 500GB gp3, 4× event throughput |
| **Storage** | Amazon S3 | ~400GB/mo evidence | $10 | $10 | 4× evidence volume |
| **Networking** | ALB | 1 ALB, ~2TB data transfer | $40 | $40 | Increased data transfer |
| **Networking** | NAT Gateway | ~400GB egress | $60 | $60 | Increased egress |
| **Container Registry** | Amazon ECR | ~5GB | $1 | $1 | Same |
| **Secrets** | AWS Secrets Manager | ~10 secrets | $4 | $4 | Same |
| **Monitoring** | Amazon CloudWatch | Metrics + logs (16 cams) | $25 | $25 | More custom metrics + log volume |
| | | **TOTAL** | **$3,526** | **$2,824** | **Savings: $702/mo (20%)** |

---

## Cost Optimization Strategies

### 1. EC2 Reserved Instances (GPU)

The GPU instance is the single largest cost item. Reserved pricing delivers significant savings:

| Commitment | g5.2xlarge (4-cam) | g5.4xlarge (8-cam) | Savings vs On-Demand |
|:----------:|-------------------:|-------------------:|:--------------------:|
| On-Demand | $958/mo | $1,916/mo | — |
| 1-Year Reserved (No Upfront) | $607/mo | $1,214/mo | **37%** |
| 1-Year Reserved (All Upfront) | $550/mo | $1,100/mo | **43%** |
| 3-Year Reserved (All Upfront) | $370/mo | $740/mo | **61%** |

> **Recommendation**: Start with 1-Year No Upfront Reserved to minimize risk. Transition to 3-Year All Upfront after validating production stability.

### 2. EC2 Spot for VLM Inference

The VLM service (scene analysis) is **interruptible** — if a Spot instance is reclaimed, pending analysis requests are simply retried from Kafka. This makes it an ideal Spot candidate.

- Spot pricing for g5.2xlarge in ap-south-1: ~$0.39/hr (~$280/mo)
- **Savings: ~$327/mo (54% vs Reserved)**
- Implementation: Run VLM on a separate Spot g5.xlarge, pipeline remains on Reserved

### 3. Fargate Spot for Non-Critical Services

The following services tolerate brief interruptions and can run on Fargate Spot (70% discount):

| Service | Standard $/mo | Spot $/mo | Savings |
|---------|-------------:|----------:|--------:|
| log-worker | $9.20 | $2.76 | $6.44 |
| evidence-svc | $18.40 | $5.52 | $12.88 |
| tts-worker | $18.40 | $5.52 | $12.88 |
| **Total** | **$46.00** | **$13.80** | **$32.20/mo** |

### 4. S3 Intelligent Tiering + Lifecycle

Evidence storage costs grow linearly with camera count and retention period:

| Retention Strategy | 100GB/mo Cost (Year 1) | Notes |
|:-------------------|----------------------:|:------|
| S3 Standard only | $27.60/yr | No optimization |
| Standard → IA (30d) | $18.00/yr | 35% savings |
| Standard → IA (30d) → Glacier (90d) | $8.40/yr | **70% savings** |
| Intelligent Tiering (auto) | $10.80/yr | Auto-optimization, small monitoring fee |

> **Recommendation**: Use explicit lifecycle rules (Standard → IA → Glacier) for predictable costs.

### 5. Scale-Down Scheduling

For deployments where 24/7 monitoring is not required:

- **Non-business hours (8 PM - 6 AM)**: Stop Fargate Spot tasks (log-worker, evidence-svc)
- **GPU instance**: Cannot easily stop/start (long boot time), keep running
- **Savings**: ~$15-20/mo on Fargate tasks (marginal)

> **Note**: Most waste-dumping occurs at night, so 24/7 operation is typically recommended.

### 6. VPC Endpoints (Avoid NAT Gateway Costs)

NAT Gateway charges $0.045/GB for data processed. VPC endpoints for S3 and ECR eliminate this:

| Endpoint | Traffic Saved | Monthly Savings |
|:---------|:-------------|:---------------:|
| S3 Gateway Endpoint | ~100GB evidence uploads | $4.50 |
| ECR VPC Endpoint | ~2GB image pulls/deploys | $0.09 |
| **Total** | | **~$5/mo** |

> VPC Gateway Endpoints for S3 are free. Interface Endpoints for ECR cost $7.20/mo each — only worthwhile at high traffic.

### 7. Right-Sizing with CloudWatch

Monitor these metrics monthly and downsize underutilized services:

| Metric | Threshold | Action |
|:-------|:----------|:-------|
| Fargate CPU utilization < 20% | Sustained 1 week | Reduce vCPU allocation |
| Fargate memory utilization < 30% | Sustained 1 week | Reduce memory allocation |
| RDS CPU < 15% | Sustained 1 month | Consider db.t3.small |
| ElastiCache hit rate > 95%, CPU < 10% | Sustained 1 month | Consider cache.t3.small |
| MSK disk utilization < 20% | Sustained 1 month | Reduce broker storage |

---

## TCO Comparison: On-Premise vs AWS

| Factor | On-Premise | AWS (4-cam Reserved) |
|--------|----------:|--------------------:|
| Server Hardware (RTX 3060 + 32GB RAM + NVMe) | $2,500 one-time | — |
| Network equipment (switch, router, UPS) | $500 one-time | — |
| Monthly power + cooling | $50/mo | — |
| Monthly internet (dedicated IP + bandwidth) | $30/mo | — |
| Monthly AWS infrastructure | — | $1,375/mo |
| **Year 1 Total** | **$3,960** | **$16,500** |
| **Year 2 Total** | **$4,920** | **$33,000** |
| **Year 3 Total** | **$5,880** | **$49,500** |
| Maintenance / Administration | Self-managed (IT staff required) | AWS managed (HA, backups, patches) |
| Availability | Single point of failure | Multi-AZ, 99.9% SLA |
| Disaster Recovery | Manual backups, no failover | Automated Multi-AZ failover, S3 durability 11 nines |
| Scaling | Buy new hardware (weeks lead time) | Add instances in minutes |
| Security Patches | Manual OS/Docker updates | Managed service patching |
| Physical Security | Office/server room | AWS data center (SOC 2, ISO 27001) |

> **Recommendation**: On-premise is **4-10x cheaper** for small deployments (4 cameras) when IT staff is available for maintenance. AWS is justified for:
> - **8+ cameras** or **multi-site** deployments (shared infrastructure)
> - **High availability** requirements (regulatory, enterprise SLA)
> - **Zero-maintenance** operations (no dedicated IT staff)
> - **Rapid scaling** needs (seasonal, event-based monitoring)
> - **Data sovereignty** requirements (specific AWS region compliance)

---

## Per-Camera Unit Economics

| Cameras | Monthly Total (Reserved) | Per Camera / Month | Per Camera / Year |
|:-------:|-------------------------:|-----------------:|------------------:|
| 4 | $1,375 | **$344** | **$4,125** |
| 8 | $1,881 | **$235** | **$2,822** |
| 16 | $2,916 | **$182** | **$2,187** |

> Cost per camera **decreases significantly at scale** due to shared GPU compute and fixed infrastructure overhead. The GPU instance (largest cost item) handles 4 cameras with headroom — scaling to 8 cameras on the same instance adds zero GPU cost.

### Break-Even Analysis: AWS vs On-Premise

| Deployment | On-Premise (per cam/mo) | AWS (per cam/mo) | AWS Premium |
|:-----------|------------------------:|------------------:|:------------|
| 4 cameras | ~$82 | $344 | 4.2× more expensive |
| 8 cameras | ~$65 | $235 | 3.6× more expensive |
| 16 cameras | ~$52 | $182 | 3.5× more expensive |

> AWS premium pays for: zero-maintenance, high availability, automated backups, instant scaling, and enterprise-grade security. For customers who value these, the premium is justified.

---

## Pricing Assumptions & Notes

1. **All prices are USD**, based on AWS ap-south-1 (Mumbai) public pricing as of April 2026
2. **Reserved Instance pricing** assumes 1-Year No Upfront commitment
3. **Data transfer** costs assume moderate usage; heavy WebRTC streaming may increase ALB/NAT costs
4. **MSK pricing** includes broker instances only; additional storage/throughput charges may apply
5. **Fargate pricing** uses Linux/x86 rates; ARM (Graviton) saves ~20% where compatible
6. **S3 costs** assume ~100GB/month per 4 cameras with lifecycle policies active
7. **Prices may vary** — always verify with the [AWS Pricing Calculator](https://calculator.aws/)
8. **Tax/GST not included** — Indian customers add 18% GST on AWS invoices

---

## Appendix: Monthly Cost Summary (All Tiers)

```
                        4-cam       8-cam       16-cam
                     Reserved    Reserved    Reserved
GPU Compute            $607       $1,214      $1,214
Fargate (CPU)          $165         $265        $420
Amazon MSK             $380         $570        $760
ElastiCache             $47          $47         $94
RDS PostgreSQL          $98         $147        $196
S3 Storage               $3           $5         $10
ALB                     $25          $30         $40
NAT Gateway             $35          $45         $60
ECR                      $1           $1          $1
Secrets Manager          $4           $4          $4
CloudWatch              $10          $15         $25
────────────────────────────────────────────────────
TOTAL                $1,375       $2,343      $2,824
Per Camera/Month       $344         $293        $177
```

---

## Neeraj's Deployment — On-Premise Reference Build

**Location**: Lake Shore Gardens, Vidyaranyapura, Bangalore

The Civilytics reference deployment runs on Neeraj's local server at Lake Shore Gardens (LSG), Bangalore. This is the first production site and the baseline for on-premise cost calculations.

### Server Specifications

| Component | Spec |
|-----------|------|
| CPU | Intel Core i5-12400F (6C/12T, 4.4 GHz) |
| RAM | 16 GB DDR4 |
| GPU | NVIDIA GeForce RTX 3060 12GB |
| Storage | 124 GB SSD (34 GB free) |
| OS | Ubuntu 24.04.4 LTS |
| CUDA | 12.x, Driver 580.126 |

### Monthly Operating Cost

| Expense | Monthly (INR) | Monthly (USD) | Notes |
|---------|-------------:|-------------:|-------|
| Server hardware | — | — | Already owned (₹75,000 one-time) |
| Electricity (250W avg, ₹8/kWh, 24/7) | ₹1,440 | $17 | Power + cooling |
| Internet (business fiber, 100 Mbps static IP) | ₹2,500 | $30 | Required for RTSP cameras |
| Domain + SSL | ₹100 | $1 | Let's Encrypt = free |
| Telegram Bot API | ₹0 | $0 | Free tier (unlimited) |
| SIP / Twilio (~100 calls/mo) | ₹500 | $6 | Free with local Asterisk PBX |
| Cloud evidence sync (Dropbox 2TB / S3) | ₹800 | $10 | Or local NAS = ₹0 |
| UPS battery replacement (annualized) | ₹200 | $2 | 600 VA UPS |
| **TOTAL OPEX** | **₹5,540** | **$66** | |

### Capacity

| Cameras | VRAM Usage | FPS | VLM | Verdict |
|:-------:|:----------:|:---:|:---:|---------|
| 2 | ~7 GB / 12 GB | 30 | Concurrent | Comfortable |
| **4** | **~9.6 GB / 12 GB** | **30** | **Time-shared** | **Sweet spot** |
| 6 | ~11 GB / 12 GB | 20 | Queued | Pushing limits |
| 8 | 12+ GB | — | — | OOM — needs second GPU |

### vs AWS (4-Camera Comparison)

| | Neeraj's Server | AWS (Reserved) |
|---|---:|---:|
| Upfront hardware | ₹75,000 ($900) | ₹0 |
| Monthly OPEX | ₹5,540 ($66) | ₹1,15,000 ($1,375) |
| **Year 1 Total** | **₹1,41,480 ($1,692)** | **₹13,80,000 ($16,500)** |
| **Year 2 Total** | **₹2,07,960 ($2,484)** | **₹27,60,000 ($33,000)** |
| **Year 3 Total** | **₹2,74,440 ($3,276)** | **₹41,40,000 ($49,500)** |
| Break-even | — | Never (on-prem always cheaper) |
| Availability | Single server, manual recovery | Multi-AZ, 99.9% SLA |
| Scaling | Buy hardware | Add instances |
| Maintenance | Self-managed | AWS managed |

> **Verdict**: On-premise is **10-15x cheaper** for single-site deployments up to 4 cameras.
> AWS is justified for multi-site, high-availability, or zero-maintenance requirements.

### Upgrade Path

| Upgrade | Cost (INR) | Benefit |
|---------|----------:|---------|
| +16 GB RAM (32 GB total) | ₹3,000 | Run all 15 services comfortably |
| +1 TB NVMe SSD | ₹5,000 | 6 months of evidence storage locally |
| +RTX 3060 (second GPU) | ₹25,000 | 8 cameras: pipeline on GPU 0, VLM on GPU 1 |
| Upgrade to RTX 4070 Ti | ₹50,000 | 12 GB + faster inference, 8 cameras single GPU |

---

## Commercial Pricing — Indian Market

### Recommended Pricing for Bangalore / Tier-1 Indian Customers

Civilytics is positioned as a **SaaS / Managed Service** for residential communities (like Lake Shore Gardens), BBMP ward offices, industrial estates, tech parks, and smart city projects.

### Pricing Tiers

#### Tier 1: Civilytics Starter (Residential Community / Small Site)

| Item | Description | Price (INR) |
|------|-------------|------------:|
| Setup fee (one-time) | Hardware procurement, installation, camera setup, network config, Keycloak user setup, ROI configuration | ₹2,00,000 |
| Hardware (customer-owned) | Server (i5 + 16GB + RTX 3060) + 4 IP cameras + 2 IP speakers + UPS + cabling | ₹2,50,000 |
| Monthly subscription | 4 cameras, SaaS license, Telegram alerts, dashboard access, evidence storage (30 days) | ₹25,000/mo |
| Annual contract | 12 months prepaid (10% discount) | ₹2,70,000/yr |
| Per additional camera | Add-on beyond 4 cameras | ₹5,000/cam/mo |
| | | |
| **Year 1 Total** | Setup + hardware + 12 months | **₹7,20,000** |
| **Year 2+ Total** | Subscription only | **₹3,00,000/yr** |

#### Tier 2: Civilytics Professional (Industrial Estate / Tech Park)

| Item | Description | Price (INR) |
|------|-------------|------------:|
| Setup fee (one-time) | Multi-zone deployment, VPN setup, SIP integration, IP speaker array, custom ROI per zone | ₹5,00,000 |
| Hardware (customer-owned) | Server (i7 + 32GB + RTX 4070 Ti) + 8 cameras + 4 speakers + managed switch + UPS | ₹5,00,000 |
| Monthly subscription | 8 cameras, priority support, SIP voice alerts, LPR, VLM analysis, evidence storage (90 days) | ₹50,000/mo |
| Annual contract | 12 months prepaid (10% discount) | ₹5,40,000/yr |
| Per additional camera | | ₹4,500/cam/mo |
| | | |
| **Year 1 Total** | Setup + hardware + 12 months | **₹15,40,000** |
| **Year 2+ Total** | Subscription only | **₹6,00,000/yr** |

#### Tier 3: Civilytics Enterprise (Smart City / Multi-Site)

| Item | Description | Price (INR) |
|------|-------------|------------:|
| Setup fee (one-time) | Multi-site architecture, AWS/hybrid deployment, custom integrations, training, SLA | ₹15,00,000 |
| Infrastructure | AWS or dedicated on-prem servers, per-site hardware | Custom quote |
| Monthly subscription | 16+ cameras, 24/7 support, SIP escalation cascade, analytics API, custom dashboards | ₹1,50,000/mo |
| Annual contract | 12 months prepaid (15% discount) | ₹15,30,000/yr |
| Per additional camera | Volume discount above 16 | ₹3,500/cam/mo |
| | | |
| **Year 1 Total** | Setup + infra + 12 months | **₹35,00,000+** |
| **Year 2+ Total** | Subscription only | **₹18,00,000/yr** |

### Margin Analysis (Bangalore Tier 1 Customer)

| Item | Customer Pays (INR) | Our Cost (INR) | Gross Margin |
|------|--------------------:|---------------:|:------------:|
| Setup fee | ₹2,00,000 | ₹50,000 (2 engineer-days) | **75%** |
| Hardware (pass-through) | ₹2,50,000 | ₹2,00,000 (procurement) | **20%** |
| Monthly subscription | ₹25,000/mo | ₹5,540/mo (OPEX) | **78%** |
| Annual subscription | ₹2,70,000/yr | ₹66,480/yr (OPEX) | **75%** |
| **Year 1 blended** | **₹7,20,000** | **₹3,16,480** | **56%** |
| **Year 2+ blended** | **₹3,00,000/yr** | **₹66,480/yr** | **78%** |

### Competitive Positioning (Bangalore Market)

| Competitor / Alternative | Price Range | Civilytics Advantage |
|--------------------------|-------------|----------------------|
| Manual CCTV monitoring (guards) | ₹30,000-50,000/mo per site | 24/7 automated, no human fatigue, evidence-grade recording |
| Generic CCTV analytics (Hikvision/Dahua) | ₹5-15 lakh setup + AMC | No waste-specific detection, no VLM, no action SGIE |
| IBM Maximo / SAP IoT | ₹50+ lakh/yr | Overkill for waste monitoring, long deployment |
| Custom ML solution (consultancy) | ₹20-50 lakh + ₹5-10 lakh/yr | 6-12 month build time, no productized platform |
| **Civilytics** | **₹7.2 lakh Y1, ₹3 lakh/yr** | **Purpose-built, <1s alerts, VLM + LPR, production-ready** |

### Target Customers in Bangalore

| Segment | Example | Cameras | Tier | Annual Value |
|---------|---------|:-------:|:----:|------------:|
| RWA (Residential) | Prestige Lakeside Habitat, Sobha Dream Acres | 4 | Starter | ₹3,00,000 |
| BBMP / Municipality | BBMP ward offices, KSPCB monitoring sites | 4-8 | Starter/Pro | ₹3-6,00,000 |
| Industrial Estate | Peenya Industrial Area, Electronic City | 8-16 | Professional | ₹6-18,00,000 |
| Smart City SPV | Bengaluru Smart City Ltd | 16+ | Enterprise | ₹18,00,000+ |
| Waste Management Co | Hasiru Dala, BWSSB transfer stations | 8 | Professional | ₹6,00,000 |
| Tech Park | Manyata, RMZ Ecoworld, Embassy | 8-16 | Professional | ₹6-18,00,000 |

### Payment Terms (Standard)

- Setup fee: 50% advance, 50% on go-live
- Hardware: 100% advance (pass-through procurement)
- Subscription: Monthly or annual prepaid
- Annual contracts: 10-15% discount
- Multi-year (3yr): 20% discount on subscription
- Multi-site: volume discount negotiable

### ROI Pitch for Customer

| Metric | Without Civilytics | With Civilytics |
|--------|-------------------:|----------------:|
| Waste dumping incidents/month | 15-30 | 2-5 (80% reduction) |
| Fine collection (per incident, BBMP) | ₹0 (no evidence) | ₹5,000-25,000 |
| Monthly fine revenue (8 incidents caught) | ₹0 | ₹40,000-2,00,000 |
| Cleanup cost savings/month | ₹0 | ₹30,000-50,000 |
| **Monthly ROI** | — | **₹70,000-2,50,000** |
| **Payback period** | — | **3-6 months** |

> **Key pitch**: Civilytics pays for itself in 3-6 months through fine collection and cleanup cost savings. After payback, it generates net revenue for the community or municipality.

---

## ROI Analysis

### Satyaki Solutions Business ROI (Per Starter Customer)

| Metric | Year 1 | Year 2 | Year 3 |
|--------|-------:|-------:|-------:|
| Revenue | ₹7,20,000 | ₹3,00,000 | ₹3,00,000 |
| Cost (OPEX + setup labor) | ₹3,16,480 | ₹66,480 | ₹66,480 |
| **Profit** | **₹4,03,520** | **₹2,33,520** | **₹2,33,520** |
| **Margin** | **56%** | **78%** | **78%** |

### Satyaki Portfolio ROI (5 Starter Customers in Bangalore)

| | Year 1 | Year 2 | Year 3 | Cumulative |
|---|-------:|-------:|-------:|----------:|
| Revenue | ₹36,00,000 | ₹15,00,000 | ₹15,00,000 | ₹66,00,000 |
| Cost | ₹15,82,400 | ₹3,32,400 | ₹3,32,400 | ₹22,47,200 |
| **Net Profit** | **₹20,17,600** | **₹11,67,600** | **₹11,67,600** | **₹43,52,800** |

> Development cost is sunk. Every new customer from Year 2 onward is **78% gross margin** recurring revenue.

### Customer's ROI (Residential Community / BBMP / Industrial Estate)

#### Monthly Financial Impact for the Customer

```
REVENUE GENERATED
  Fines collected (8 caught × ₹10,000 avg)          = ₹80,000/mo

COSTS AVOIDED
  Cleanup cost savings (80% reduction)               = ₹40,000/mo
  Guard cost reduction (1 fewer guard)                = ₹20,000/mo
  Health/legal liability reduction                    = unquantified
                                                       ──────────
  Total monthly benefit                              = ₹1,40,000/mo
  WasteWatch subscription                            = ₹25,000/mo
                                                       ──────────
  NET MONTHLY GAIN                                   = ₹1,15,000/mo
```

#### Payback Calculation

| | Amount |
|---|------:|
| Year 1 cost (setup + hardware + 12 months) | ₹7,20,000 |
| Year 1 benefit (₹1,40,000 × 12 months) | ₹16,80,000 |
| **Year 1 net ROI** | **₹9,60,000 (133%)** |
| **Payback period** | **5.1 months** |

After payback, every month is **₹1,15,000 net positive** for the customer.

#### Multi-Year Customer ROI

| Year | Cost | Benefit | Net Gain | Cumulative |
|:----:|-----:|--------:|---------:|---------:|
| 1 | ₹7,20,000 | ₹16,80,000 | ₹9,60,000 | ₹9,60,000 |
| 2 | ₹3,00,000 | ₹16,80,000 | ₹13,80,000 | ₹23,40,000 |
| 3 | ₹3,00,000 | ₹16,80,000 | ₹13,80,000 | ₹37,20,000 |

> 3-year customer ROI: **₹37.2 lakh net gain** on a ₹13.2 lakh total investment = **282% ROI**.

### ROI by Customer Segment (Bangalore Market)

| Segment | Fine/Incident | Catches/Month | Monthly Benefit | Subscription | Payback |
|---------|-------------:|:------------:|----------------:|-------------:|--------:|
| **RWA** (Prestige, Sobha) | ₹5,000 | 6 | ₹70,000 | ₹25,000 | **6 months** |
| **BBMP Ward Office** | ₹10,000 | 10 | ₹1,40,000 | ₹25,000 | **4 months** |
| **Industrial Estate** (Peenya) | ₹25,000 | 8 | ₹2,40,000 | ₹50,000 | **3 months** |
| **Tech Park** (Manyata, RMZ) | ₹15,000 | 6 | ₹1,30,000 | ₹50,000 | **5 months** |
| **Smart City SPV** | ₹10,000 | 20 | ₹2,40,000 | ₹1,50,000 | **6 months** |

### Without vs With Civilytics

| Metric | Without | With | Improvement |
|--------|--------:|-----:|:-----------:|
| Dumping incidents/month | 20 | 4 | **80% reduction** |
| Fines collected/month | ₹0 | ₹80,000 | **New revenue** |
| Cleanup cost/month | ₹50,000 | ₹10,000 | **80% savings** |
| Evidence for prosecution | None | HD video + plate + VLM report | **Court-grade** |
| Detection time | Hours (manual) | <5 seconds | **99.9% faster** |
| Guard requirement | 3 guards/site | 1 guard/site | **67% reduction** |
| Repeat offender tracking | None | License plate database | **Automated** |
| Operating hours | 8 hrs (guard shift) | 24/7/365 | **3x coverage** |

### One-Slide Pitch

> **"For ₹25,000/month, Civilytics catches 8+ illegal dumpers that you're currently missing. At BBMP fine rates, that's ₹80,000 to ₹2,00,000/month in recoverable fines alone — plus ₹40,000/month in cleanup savings. The system pays for itself in 5 months. After that, it's pure revenue for your municipality."**

---

*Copyright 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.*
*Civilytics is a product of Satyaki Solutions Pvt Ltd — https://satyaki.co.in*

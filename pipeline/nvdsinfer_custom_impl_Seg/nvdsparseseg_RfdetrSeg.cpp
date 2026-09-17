/*
 * Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
 *
 * Custom DeepStream instance-segmentation parser for RF-DETR-Seg-Nano
 * (WasteWatch seg-v7/v8, 4-class checkpoint: 0=Waste-watch/background,
 * 1=bag, 2=person, 3=waste).
 *
 * Unlike the bbox-only rfdetr.onnx (pipeline/nvdsinfer_custom_impl_Yolo),
 * this model is exported WITHOUT a fused DeepStreamOutput head — there is
 * no clean way to concat a [100,96,96] mask tensor into the same
 * single-tensor YOLO-style [N,Q,6] trick the bbox model uses, so this
 * parser reads the three raw ONNX outputs directly and does the full
 * decode itself:
 *
 *   dets   [1,100,4]     cxcywh, normalised to [0,1] against the 384x384
 *                        network input (standard DETR box parameterisation).
 *   labels [1,100,4]     per-query class logits. Channel 0 is the
 *                        "Waste-watch" background/ignore class the
 *                        training run used as class-index 0 (see
 *                        nvinfer_rfdetr_seg.txt's comment on RF-DETR's
 *                        off-by-one class-index convention — this repo
 *                        has been bitten by exactly this before with the
 *                        bbox model). Channels 1,2,3 map directly to
 *                        bag/person/waste.
 *   masks  [1,100,96,96] per-query mask LOGITS over the *full* 384x384
 *                        input at 1/4 resolution (96 = 384/4) — NOT
 *                        per-ROI crops like Mask R-CNN/TAO MaskRCNN. We
 *                        crop the region under each kept box out of this
 *                        full-frame grid, sigmoid it, and hand DeepStream
 *                        that crop as the instance mask (mask_width/
 *                        mask_height == the box's footprint in grid
 *                        cells, sized to at least 1x1).
 *
 * IMPORTANT — this parser previously assumed a 5-channel/stride-6 labels
 * layout (bg=0, stray unused "a" class=1, bag=2, person=3, waste=4), a
 * relic of runs_seg_v2's checkpoint which had 5 class_names including a
 * never-annotated "a" class. runs_seg_v7/v8 were trained on a clean
 * 4-category COCO export (Waste-watch/bag/person/waste, no stray class),
 * so the exported ONNX `labels` output is genuinely [1,100,4] — confirmed
 * via `onnx.load(...).graph.output`. Reading with the old stride-6 offset
 * (`labels + q*6`) walked into the wrong query's data for every q>=1, and
 * for q>=67 read past the end of the 400-float buffer entirely. This
 * silently produced near-zero/garbage waste-channel probabilities that
 * never crossed any confidence threshold (confirmed live: raw_class_hist
 * in wastewatch.metadata showed exactly zero waste detections across
 * multiple 400-frame windows even at pre-cluster-threshold=0.05, on two
 * independently retrained checkpoints — a symptom that pointed at a
 * structural bug, not a data/training problem). Fixed to stride 4 and
 * channels {bg=0, bag=1, person=2, waste=3}.
 */

#include "nvdsinfer_custom_impl.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <iostream>
#include <vector>

namespace {

constexpr int kNumQueries = 100;
constexpr int kNumLogitChannels = 5;   // labels last dim (matches [1,100,5] ONNX output)
constexpr int kMaskGrid = 96;          // masks spatial dim (netW/4, netH/4)
// v12 retrain added old_waste back as a real class (bag/person/waste ->
// bag/new_waste/old_waste/person). rfdetr's COCO loader drops the
// unannotated background category (id 0, "Waste-watch"), then assigns
// real-class indices alphabetically by name (confirmed against the
// working 3-class deploy: category ids were bag=1/waste=2/person=4 but
// live detections matched channel order bag=0/person=1/waste=2, i.e.
// alphabetical, not id-sorted), and appends the implicit "no object"
// class at the END (standard DETR num_classes+1 convention).
// Alphabetical 4-class order: bag, new_waste, old_waste, person.
constexpr int kBackgroundChannelLo = 4;  // "no object" -- last channel
constexpr int kBackgroundChannelHi = 4;
constexpr int kRealClassChannelsLo = 0;
constexpr int kRealClassChannelsHi = 3; // inclusive: 0=bag,1=new_waste,2=old_waste,3=person
constexpr unsigned int kOldWasteChannel = 2; // ignored entirely, see below

inline float sigmoidf(float x) { return 1.0f / (1.0f + std::exp(-x)); }

inline float clampf(float v, float lo, float hi) {
  return std::max(lo, std::min(hi, v));
}

const NvDsInferLayerInfo* findLayer(
    std::vector<NvDsInferLayerInfo> const& layers, const char* name) {
  for (auto const& l : layers) {
    if (l.layerName && std::strcmp(l.layerName, name) == 0) {
      return &l;
    }
  }
  return nullptr;
}

}  // namespace

extern "C" bool NvDsInferParseCustomRfdetrSeg(
    std::vector<NvDsInferLayerInfo> const& outputLayersInfo,
    NvDsInferNetworkInfo const& networkInfo,
    NvDsInferParseDetectionParams const& detectionParams,
    std::vector<NvDsInferInstanceMaskInfo>& objectList) {
  const NvDsInferLayerInfo* detsLayer = findLayer(outputLayersInfo, "dets");
  const NvDsInferLayerInfo* labelsLayer = findLayer(outputLayersInfo, "labels");
  const NvDsInferLayerInfo* masksLayer = findLayer(outputLayersInfo, "masks");

  if (!detsLayer || !labelsLayer || !masksLayer) {
    std::cerr << "NvDsInferParseCustomRfdetrSeg: missing one of "
                 "dets/labels/masks output layers"
              << std::endl;
    return false;
  }

  const float* dets = reinterpret_cast<const float*>(detsLayer->buffer);
  const float* labels = reinterpret_cast<const float*>(labelsLayer->buffer);
  const float* masks = reinterpret_cast<const float*>(masksLayer->buffer);

  const float netW = static_cast<float>(networkInfo.width);
  const float netH = static_cast<float>(networkInfo.height);

  objectList.clear();
  objectList.reserve(kNumQueries);

  for (int q = 0; q < kNumQueries; ++q) {
    const float* qLogits = labels + q * kNumLogitChannels;

    int bestChannel = kRealClassChannelsLo;
    float bestProb = sigmoidf(qLogits[kRealClassChannelsLo]);
    for (int c = kRealClassChannelsLo + 1; c <= kRealClassChannelsHi; ++c) {
      float p = sigmoidf(qLogits[c]);
      if (p > bestProb) {
        bestProb = p;
        bestChannel = c;
      }
    }
    // Also check background/ignore (Waste-watch or stray 'a') isn't
    // stronger than every real class — if it is, this query is not a
    // real detection at all.
    float bgProb = sigmoidf(qLogits[kBackgroundChannelLo]);
    for (int c = kBackgroundChannelLo + 1; c <= kBackgroundChannelHi; ++c) {
      bgProb = std::max(bgProb, sigmoidf(qLogits[c]));
    }
    if (bgProb >= bestProb) {
      continue;
    }

    const unsigned int classId = static_cast<unsigned int>(bestChannel - kRealClassChannelsLo); // 0=bag,1=new_waste,2=old_waste,3=person
    // old_waste (channel 2) is explicitly ignored end-to-end -- excluded
    // here at the parser so it never becomes a NvDsInferInstanceMaskInfo
    // object at all: no bbox, no OSD render, no tracker ID, never reaches
    // the FSM/alerting Python side. Still competes fairly in the argmax
    // above (a real old_waste-looking region won't get miscounted as
    // bag/new_waste/person just because we drop it here).
    if (classId == kOldWasteChannel) {
      continue;
    }
    if (classId >= detectionParams.perClassPreclusterThreshold.size()) {
      continue;
    }
    if (bestProb < detectionParams.perClassPreclusterThreshold[classId]) {
      continue;
    }

    // --- box: cxcywh normalised -> pixel xyxy, clamped to network dims.
    const float* box = dets + q * 4;
    float cx = box[0] * netW;
    float cy = box[1] * netH;
    float w = box[2] * netW;
    float h = box[3] * netH;
    float x1 = clampf(cx - 0.5f * w, 0.0f, netW);
    float y1 = clampf(cy - 0.5f * h, 0.0f, netH);
    float x2 = clampf(cx + 0.5f * w, 0.0f, netW);
    float y2 = clampf(cy + 0.5f * h, 0.0f, netH);
    float boxW = clampf(x2 - x1, 0.0f, netW);
    float boxH = clampf(y2 - y1, 0.0f, netH);
    if (boxW < 1.0f || boxH < 1.0f) {
      continue;
    }

    // --- mask: crop the box's footprint out of the 96x96 full-frame
    // grid for this query, sigmoid it in place.
    int gx1 = static_cast<int>(std::floor(x1 / netW * kMaskGrid));
    int gy1 = static_cast<int>(std::floor(y1 / netH * kMaskGrid));
    int gx2 = static_cast<int>(std::ceil(x2 / netW * kMaskGrid));
    int gy2 = static_cast<int>(std::ceil(y2 / netH * kMaskGrid));
    gx1 = std::max(0, std::min(gx1, kMaskGrid - 1));
    gy1 = std::max(0, std::min(gy1, kMaskGrid - 1));
    gx2 = std::max(gx1 + 1, std::min(gx2, kMaskGrid));
    gy2 = std::max(gy1 + 1, std::min(gy2, kMaskGrid));
    unsigned int maskW = static_cast<unsigned int>(gx2 - gx1);
    unsigned int maskH = static_cast<unsigned int>(gy2 - gy1);

    const float* qMask = masks + static_cast<size_t>(q) * kMaskGrid * kMaskGrid;
    float* maskData = new float[maskW * maskH];
    for (unsigned int r = 0; r < maskH; ++r) {
      for (unsigned int c = 0; c < maskW; ++c) {
        float logit = qMask[(gy1 + r) * kMaskGrid + (gx1 + c)];
        maskData[r * maskW + c] = sigmoidf(logit);
      }
    }

    NvDsInferInstanceMaskInfo info;
    info.classId = classId;
    info.left = x1;
    info.top = y1;
    info.width = boxW;
    info.height = boxH;
    info.detectionConfidence = bestProb;
    info.mask = maskData;
    info.mask_width = maskW;
    info.mask_height = maskH;
    info.mask_size = maskW * maskH * sizeof(float);

    objectList.push_back(info);
  }

  return true;
}

CHECK_CUSTOM_INSTANCE_MASK_PARSE_FUNC_PROTOTYPE(NvDsInferParseCustomRfdetrSeg);

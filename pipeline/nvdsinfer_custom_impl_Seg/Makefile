################################################################################
# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
#
# Build for libnvdsinfer_custom_impl_Seg.so — mirrors
# pipeline/nvdsinfer_custom_impl_Yolo/Makefile's DeepStream SDK
# include/link paths, trimmed to what this parser actually needs
# (no CUDA kernels, no OpenCV).
################################################################################

CUDA_VER?=
ifeq ($(CUDA_VER),)
	$(error "CUDA_VER is not set")
endif

CC:= g++

CFLAGS:= -Wall -std=c++14 -shared -fPIC -Wno-error=deprecated-declarations -O2
CFLAGS+= -I/opt/nvidia/deepstream/deepstream/sources/includes -I/usr/local/cuda-$(CUDA_VER)/include

LIBS:= -L/usr/local/cuda-$(CUDA_VER)/lib64 -lcudart
LFLAGS:= -shared -Wl,--start-group $(LIBS) -Wl,--end-group

SRCFILES:= nvdsparseseg_RfdetrSeg.cpp
TARGET_LIB:= libnvdsinfer_custom_impl_Seg.so
TARGET_OBJS:= $(SRCFILES:.cpp=.o)

all: $(TARGET_LIB)

%.o: %.cpp Makefile
	$(CC) -c -o $@ $(CFLAGS) $<

$(TARGET_LIB) : $(TARGET_OBJS)
	$(CC) -o $@ $(TARGET_OBJS) $(LFLAGS)

clean:
	rm -rf $(TARGET_LIB)
	rm -rf $(TARGET_OBJS)

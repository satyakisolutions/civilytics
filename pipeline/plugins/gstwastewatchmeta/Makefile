# WasteWatch Custom GStreamer Plugin - Build
# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd

CC := gcc
PKGFLAGS := $(shell pkg-config --cflags --libs gstreamer-1.0 gstreamer-base-1.0)

DS_ROOT := /opt/nvidia/deepstream/deepstream
DS_INC := -I$(DS_ROOT)/sources/includes
DS_LIBS := -L$(DS_ROOT)/lib -lnvdsgst_meta -lnvds_meta -lnvdsgst_helper

CFLAGS := -fPIC -Wall -Wextra -O2 $(DS_INC) $(PKGFLAGS)
LDFLAGS := -shared $(DS_LIBS)

TARGET := libgstwastewatchmeta.so

all: $(TARGET)

$(TARGET): gstwastewatchmeta.c gstwastewatchmeta.h
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

install: $(TARGET)
	cp $(TARGET) $(DS_ROOT)/lib/gst-plugins/
	@echo "Installed to $(DS_ROOT)/lib/gst-plugins/"

clean:
	rm -f $(TARGET)

.PHONY: all install clean

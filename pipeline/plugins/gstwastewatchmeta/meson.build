project('gstwastewatchmeta', 'c',
  version : '1.0.0',
  default_options : ['warning_level=2'])

gst_dep = dependency('gstreamer-1.0')
gst_base_dep = dependency('gstreamer-base-1.0')

# DeepStream paths
ds_inc = include_directories('/opt/nvidia/deepstream/deepstream/sources/includes')
ds_lib = '/opt/nvidia/deepstream/deepstream/lib'

shared_library('gstwastewatchmeta',
  'gstwastewatchmeta.c',
  dependencies : [gst_dep, gst_base_dep],
  include_directories : ds_inc,
  link_args : ['-L' + ds_lib, '-lnvdsgst_meta', '-lnvds_meta', '-lnvdsgst_helper'],
  install : true,
  install_dir : '/opt/nvidia/deepstream/deepstream/lib/gst-plugins/',
)

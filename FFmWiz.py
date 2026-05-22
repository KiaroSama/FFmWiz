from __future__ import annotations

import datetime
import concurrent.futures
import json
import logging
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass


# Search tag for the large in-code FFmpeg reference:
# FULL_FFMPEG_FORMAT_CODEC_LISTS
#
# Use this tag in your editor if you want to quickly find the long comment
# block that lists FFmpeg formats, muxers, demuxers, encoders, and decoders.


# ============================================================
# Default FFmpeg options from the command you requested before.
# Change these values here if you want different fixed defaults.
# ============================================================

GPU_DEVICE_INDEX = 0  # NVIDIA GPU index (0,1,2,...)
OVERWRITE_OUTPUT = True  # FFmpeg overwrite mode (-y or -n)

# Color Range / range metadata:
# COLOR_RANGE options: "tv"=limited, "pc"=full, "unknown"=unspecified
# SETPARAMS_RANGE options: "tv"=limited, "pc"=full, "auto", "unknown"
COLOR_RANGE = "tv"
SETPARAMS_RANGE = "tv"

# Pixel formats:
# CUDA_FORMAT options commonly used with NVENC: "nv12", "p010le"
# CPU_FORMAT options commonly used: "yuv420p", "yuv422p", "yuv444p", "nv12"
CUDA_FORMAT = "nv12"
CPU_FORMAT = "yuv420p"

# Sample aspect ratio:
# SAR options/examples: "1", "4/3", "16/15", "64/45"
FORCE_SAR = "1"

# MP4/MOV web playback optimization:
# MOVFLAGS options/examples: "+faststart", "empty_moov", "frag_keyframe", "+faststart+use_metadata_tags"
MOVFLAGS = "+faststart"

# NVENC defaults:
# NVENC_PRESET options: p1,p2,p3,p4,p5,p6,p7 (p1 fastest, p7 slowest/better)
# NVENC_TUNE options: hq,ll,ull,lossless
# NVENC_RC options: constqp,cbr,vbr,cbr_hq,vbr_hq
# NVENC_HEVC_PROFILE options: main,main10,rext
NVENC_PRESET = "p4"
NVENC_TUNE = "hq"
NVENC_RC = "vbr"
NVENC_HEVC_PROFILE = "main"

# CPU encoder defaults:
# CPU_PRESET options for x264/x265: ultrafast,superfast,veryfast,faster,fast,medium,slow,slower,veryslow
CPU_PRESET = "medium"

# Audio defaults:
# DEFAULT_AUDIO_CODEC options depend on your FFmpeg build; common: aac,libopus,libmp3lame,flac,copy
# AUDIO_CHANNELS options/examples: 1=mono, 2=stereo, 6=5.1; set None to keep source channel layout
# AUDIO_SAMPLE_RATE options/examples: None=auto/source, 44100, 48000, 96000
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_AUDIO_BITRATE_KBPS = 128
AUDIO_CHANNELS = 2
AUDIO_SAMPLE_RATE: int | None = None

# Speed/reverse editor defaults. These modes always re-encode the affected
# stream because timestamp reversal and tempo changes cannot be stream-copied.
DEFAULT_SPEED_FACTOR = 1.0
MIN_SPEED_FACTOR = 0.10
MAX_SPEED_FACTOR = 8.0
DEFAULT_SPEED_AUDIO_BITRATE_KBPS = DEFAULT_AUDIO_BITRATE_KBPS
REVERSE_SEGMENT_SECONDS = 60.0

# Video defaults:
# DEFAULT_VIDEO_CODEC aliases supported by this script: H265,H264,AV1,VP9,MPEG4,copy
DEFAULT_VIDEO_CODEC = "H265"
DEFAULT_OUTPUT_VIDEO_BITRATE_KBPS = 400

# ============================================================
# FULL_FFMPEG_FORMAT_CODEC_LISTS
# FFmpeg format and codec reference.
#
# Important:
# FFmpeg support is build-specific. A codec/format can exist in FFmpeg
# documentation but still be unavailable in your installed ffmpeg.exe if that
# build was compiled without the required library. The script therefore reads
# live runtime lists with:
#   ffmpeg -hide_banner -muxers
#   ffmpeg -hide_banner -encoders
# and stores them in:
#   answers["muxers"], answers["video_encoders"], answers["audio_encoders"]
#
# To inspect your exact local build manually:
#   ffmpeg -hide_banner -formats
#   ffmpeg -hide_banner -muxers
#   ffmpeg -hide_banner -demuxers
#   ffmpeg -hide_banner -codecs
#   ffmpeg -hide_banner -encoders
#   ffmpeg -hide_banner -decoders
#
# The lists below are intentionally large and visible, but they are still a
# source-code reference. The only truly complete list for your machine is the
# runtime list reported by your own ffmpeg.exe. This script uses those runtime
# lists for validation and uses the shorter COMMON_* lists only for cleaner
# on-screen prompts.
#
# Reference output muxers / container format names:
#   3g2,3gp,4xm,a64,ac3,adts,adx,aiff,alp,alsa,amr,amv,apm,apng,argo_asf,
#   asf,ass,ast,au,avi,avif,avm2,avs2,bit,bmv,caf,cavsvideo,codec2,codec2raw,
#   crc,dash,data,daud,dfpwm,dirac,dnxhd,dts,dv,eac3,f4v,ffmetadata,fifo,
#   fifo_test,film_cpk,filmstrip,fits,flac,flv,framecrc,framehash,framemd5,
#   g722,g723_1,g726,g726le,gif,gsm,gxf,h261,h263,h264,hash,hds,hevc,hls,
#   ico,ilbc,image2,image2pipe,ipod,ircam,ismv,ivf,jacosub,kvag,latm,lrc,m4v,
#   matroska,md5,microdvd,mjpeg,mkvtimestamp_v2,mlp,mmf,mov,mp2,mp3,mp4,mpeg,
#   mpeg1video,mpeg2video,mpegts,mpjpeg,mxf,mxf_d10,mxf_opatom,null,nut,obu,
#   oga,ogg,ogv,oma,opus,psp,rawvideo,rm,roq,rtp,rtsp,s16be,s16le,s24be,s24le,
#   s32be,s32le,s8,sap,sbc,scc,segment,smjpeg,smoothstreaming,sox,spdif,
#   spx,srt,stream_segment,streamhash,sup,svcd,swf,tee,tg2,tgp,truehd,tta,
#   u16be,u16le,u24be,u24le,u32be,u32le,u8,uncodedframecrc,vc1,vc1test,
#   voc,w64,wav,webm,webm_chunk,webm_dash_manifest,webp,webvtt,wsaud,wsvqa,
#   wtv,wv,yuv4mpegpipe
#
# Reference input demuxers / source format names:
#   aa,aac,aax,ac3,ace,acm,act,adf,adp,ads,adx,aea,afc,aiff,aix,alp,amr,amrnb,
#   amrwb,anm,apac,apc,ape,apm,apng,aptx,aptx_hd,aqtitle,argo_asf,argo_brp,
#   argo_cvg,asf,asf_o,ass,ast,au,av1,av2,avi,avisynth,avr,avs,avs2,avs3,
#   bethsoftvid,bfi,bfstm,bink,bintext,bit,bitpacked,bmv,boa,bonk,c93,caf,
#   cavsvideo,cdg,cdxl,cine,codec2,codec2raw,concat,dash,data,daud,dcstr,dds,
#   derf,dfa,dfpwm,dhav,dirac,dnxhd,dsf,dsicin,dss,dts,dtshd,dv,dvbsub,dvbtxt,
#   dxa,ea,ea_cdata,eac3,epaf,ffmetadata,film_cpk,filmstrip,fits,flac,flic,
#   flv,fourxm,frm,fsb,fwse,g722,g723_1,g726,g726le,g729,gdv,genh,gif,grpc,
#   gsm,gxf,h261,h263,h264,hca,hcom,hevc,hls,hnm,ico,idcin,idf,iff,ifv,ilbc,
#   image2,image2pipe,ingenient,ipmovie,ipu,ircam,iss,iv8,ivf,ivr,jacosub,jv,
#   kux,kvag,laf,libgme,libmodplug,live_flv,lmlm4,loas,lrc,luodat,lvf,lxf,m4v,
#   matroska,mgsts,microdvd,mjpeg,mjpeg_2000,mlp,mlv,mm,mmf,mods,moflex,mov,
#   mp3,mpc,mpc8,mpeg,mpegts,mpegtsraw,mpegvideo,mpjpeg,mpl2,mpsub,msf,msnwc_tcp,
#   msp,mtaf,mtv,musx,mv,mvi,mxf,mxg,nc,nistsphere,nsp,nsv,nut,nuv,obu,ogg,oma,
#   paf,pcm_alaw,pcm_f32be,pcm_f32le,pcm_f64be,pcm_f64le,pcm_mulaw,pcm_s16be,
#   pcm_s16le,pcm_s24be,pcm_s24le,pcm_s32be,pcm_s32le,pcm_s8,pcm_u16be,pcm_u16le,
#   pcm_u24be,pcm_u24le,pcm_u32be,pcm_u32le,pcm_u8,pcm_vidc,pjs,psxstr,pva,pvf,
#   qcp,r3d,rawvideo,realtext,redspark,rl2,rm,roq,rpl,rsd,rso,rtp,rtsp,s337m,
#   sami,sap,sbc,sbg,scc,sdns,sdp,sdr2,sds,sdx,segafilm,ser,shorten,siff,simbiosis_imx,
#   sln,smacker,smjpeg,smush,sol,sox,spdif,srt,stl,str,subviewer,subviewer1,sup,
#   svag,svs,swf,tak,tedcaptions,thp,tiertexseq,tmv,truehd,tta,tty,txd,ty,u16be,
#   u16le,u24be,u24le,u32be,u32le,u8,v210,v210x,vag,vc1,vc1test,vidc,vividas,
#   vivo,vmd,vobsub,voc,vpk,vplayer,vqf,w64,wady,wav,wc3movie,webm_dash_manifest,
#   webvtt,wsaud,wsd,wsvqa,wtv,wv,wve,xa,xbin,xmd,xmv,xvag,xwma,yop,yuv4mpegpipe
#
# Reference video encoders / codec names:
#   a64multi,a64multi5,alias_pix,amv,apng,asv1,asv2,av1_nvenc,av1_qsv,av1_vaapi,
#   bitpacked,bmp,cfhd,cinepak,cljr,comfortnoise,dnxhd,dpx,dvvideo,exr,ffv1,
#   ffvhuff,flv,gif,h261,h263,h263_v4l2m2m,h263p,h264_amf,h264_mf,h264_nvenc,
#   h264_qsv,h264_v4l2m2m,h264_vaapi,hap,hdr,hevc_amf,hevc_mf,hevc_nvenc,hevc_qsv,
#   hevc_v4l2m2m,hevc_vaapi,huffyuv,jpeg2000,jpegls,libaom-av1,libopenh264,
#   libopenjpeg,librav1e,librsvg,libsvtav1,libtheora,libvpx,libvpx-vp9,libwebp,
#   libwebp_anim,libx264,libx264rgb,libx265,libxvid,ljpeg,magicyuv,mjpeg,mjpeg_qsv,
#   mjpeg_vaapi,mpeg1video,mpeg2_qsv,mpeg2_vaapi,mpeg2video,mpeg4,mpeg4_v4l2m2m,
#   msmpeg4v2,msmpeg4v3,msvideo1,pam,pbm,pcx,pfm,pgm,pgmyuv,phm,png,ppm,prores,
#   prores_aw,prores_ks,qoi,qtrle,r10k,r210,rawvideo,roq,rv10,rv20,sgi,snow,
#   speedhq,sunrast,svq1,targa,tiff,utvideo,v210,v308,v408,v410,vc2,wrapped_avframe,
#   wmv1,wmv2,xbm,xface,xwd,y41p,yuv4,zlib,zmbv
#
# Reference audio encoders / codec names:
#   aac,ac3,ac3_fixed,adpcm_adx,adpcm_argo,adpcm_g722,adpcm_g726,adpcm_g726le,
#   adpcm_ima_alp,adpcm_ima_amv,adpcm_ima_apm,adpcm_ima_qt,adpcm_ima_ssi,
#   adpcm_ima_wav,adpcm_ima_ws,adpcm_ms,adpcm_swf,adpcm_yamaha,alac,aptx,aptx_hd,
#   comfortnoise,dfpwm,dts,eac3,flac,g723_1,libcodec2,libgsm,libgsm_ms,libilbc,
#   libmp3lame,libopencore_amrnb,libopus,libshine,libspeex,libtwolame,libvo_amrwbenc,
#   libvorbis,mlp,mp2,mp2fixed,nellymoser,opus,pcm_alaw,pcm_bluray,pcm_dvd,
#   pcm_f32be,pcm_f32le,pcm_f64be,pcm_f64le,pcm_mulaw,pcm_s16be,pcm_s16be_planar,
#   pcm_s16le,pcm_s16le_planar,pcm_s24be,pcm_s24daud,pcm_s24le,pcm_s24le_planar,
#   pcm_s32be,pcm_s32le,pcm_s32le_planar,pcm_s64be,pcm_s64le,pcm_s8,pcm_s8_planar,
#   pcm_u16be,pcm_u16le,pcm_u24be,pcm_u24le,pcm_u32be,pcm_u32le,pcm_u8,real_144,
#   roq_dpcm,s302m,sbc,sonic,sonicls,truehd,tta,vorbis,wavpack,wmav1,wmav2
#
# Reference subtitle encoders / codec names:
#   ass,dvbsub,dvdsub,mov_text,srt,ssa,subrip,text,ttml,webvtt,xsub
#
# Reference video decoders / codec names:
#   aasc,aic,alias_pix,agm,aic,amv,anm,ansi,apng,arbc,argo,asv1,asv2,aura,aura2,
#   av1,avrn,avrp,avs,avs2,avs3,bethsoftvid,bfi,binkvideo,bintext,bitpacked,bmp,
#   bmv_video,brender_pix,c93,cavs,cdgraphics,cdtoons,cdxl,cfhd,cinepak,clearvideo,
#   cljr,cllc,comfortnoise,cpia,cscd,cyuv,dds,dfa,dirac,dnxhd,dpx,dsicinvideo,
#   dvvideo,dxa,dxtory,dxv,eacmv,eamad,eatgq,eatgv,eatqi,eightbps,escape124,
#   escape130,exr,ffv1,ffvhuff,fic,fits,flashsv,flashsv2,flic,flv,fmvc,fraps,
#   frwu,g2m,gdv,gem,gif,h261,h263,h263i,h263p,h264,hap,hca,hevc,hnm4video,hq_hqa,
#   hqx,huffyuv,imm4,imm5,indeo2,indeo3,indeo4,indeo5,interplayvideo,jpeg2000,
#   jpegls,jv,kgv1,kmvc,lagarith,loco,lscr,m101,mad,mdec,mimic,mjpeg,mjpegb,mmvideo,
#   mobiclip,motionpixels,mpeg1video,mpeg2video,mpeg4,mpegvideo,msa1,mscc,msmpeg4v1,
#   msmpeg4v2,msmpeg4v3,msrle,mss1,mss2,msvideo1,mszh,mts2,mv30,mvc1,mvc2,mvdv,
#   mvha,mwsc,mxpeg,notchlc,nuv,paf_video,pam,pbm,pcx,pfm,pgm,pgmyuv,pgx,phm,
#   photocd,pictor,pixlet,png,ppm,prores,prosumer,psd,ptx,qdraw,qoi,qpeg,qtrle,
#   r10k,r210,rasc,rawvideo,rl2,roq,rv10,rv20,rv30,rv40,sanm,screenpresso,sga,
#   sgi,sgirle,sheervideo,smackvideo,smc,smvjpeg,snow,sp5x,speedhq,srgc,sunrast,
#   svq1,svq3,targa,targa_y216,tdsc,theora,thp,tiertexseq,tiff,tmv,truevision,
#   truemotion1,truemotion2,truemotion2rt,tscc,tscc2,txd,ulti,utvideo,v210,v210x,
#   v308,v408,v410,vb,vble,vc1,vc1image,vcr1,vmnc,vp3,vp4,vp5,
#   vp6,vp6a,vp6f,vp7,vp8,vp9,vqa,webp,wmv1,wmv2,wmv3,wmv3image,wnv1,wrapped_avframe,
#   xan_wc3,xan_wc4,xbin,xbm,xface,xl,xpm,xwd,xxan,y41p,ylc,yop,yuv4,zerocodec,zlib,zmbv
#
# Reference audio decoders / codec names:
#   8svx_exp,8svx_fib,aac,aac_fixed,aac_latm,ac3,ac3_fixed,acelp_kelvin,adpcm_4xm,
#   adpcm_adx,adpcm_afc,adpcm_agm,adpcm_aica,adpcm_argo,adpcm_ct,adpcm_dtk,
#   adpcm_ea,adpcm_ea_maxis_xa,adpcm_ea_r1,adpcm_ea_r2,adpcm_ea_r3,adpcm_ea_xas,
#   adpcm_g722,adpcm_g726,adpcm_g726le,adpcm_ima_acorn,adpcm_ima_alp,adpcm_ima_amv,
#   adpcm_ima_apc,adpcm_ima_apm,adpcm_ima_cunning,adpcm_ima_dat4,adpcm_ima_dk3,
#   adpcm_ima_dk4,adpcm_ima_ea_eacs,adpcm_ima_ea_sead,adpcm_ima_iss,adpcm_ima_moflex,
#   adpcm_ima_mtf,adpcm_ima_oki,adpcm_ima_qt,adpcm_ima_rad,adpcm_ima_smjpeg,
#   adpcm_ima_ssi,adpcm_ima_wav,adpcm_ima_ws,adpcm_ms,adpcm_mtaf,adpcm_psx,
#   adpcm_sbpro_2,adpcm_sbpro_3,adpcm_sbpro_4,adpcm_swf,adpcm_thp,adpcm_thp_le,
#   adpcm_vima,adpcm_xa,adpcm_xmd,adpcm_yamaha,alac,als,amrnb,amrwb,ape,aptx,aptx_hd,
#   atrac1,atrac3,atrac3al,atrac3p,atrac3pal,atrac9,binkaudio_dct,binkaudio_rdft,
#   bmv_audio,bonk,comfortnoise,cook,derf_dpcm,dfpwm,dolby_e,dsd_lsbf,dsd_lsbf_planar,
#   dsd_msbf,dsd_msbf_planar,dsicinaudio,dss_sp,dst,dvaudio,eac3,evrc,fastaudio,
#   flac,ftr,g723_1,g729,gremlin_dpcm,gsm,gsm_ms,hca,hcom,iac,ilbc,imc,interplay_acm,
#   mace3,mace6,metasound,misc4,mlp,mp1,mp1float,mp2,mp2float,mp3,mp3adu,mp3adufloat,
#   mp3float,mp3on4,mp3on4float,mpegh_3d_audio,musepack7,musepack8,nellymoser,on2avc,
#   opus,paf_audio,pcm_alaw,pcm_bluray,pcm_dvd,pcm_f16le,pcm_f24le,pcm_f32be,pcm_f32le,
#   pcm_f64be,pcm_f64le,pcm_lxf,pcm_mulaw,pcm_s16be,pcm_s16be_planar,pcm_s16le,
#   pcm_s16le_planar,pcm_s24be,pcm_s24daud,pcm_s24le,pcm_s24le_planar,pcm_s32be,
#   pcm_s32le,pcm_s32le_planar,pcm_s64be,pcm_s64le,pcm_s8,pcm_s8_planar,pcm_sga,
#   pcm_u16be,pcm_u16le,pcm_u24be,pcm_u24le,pcm_u32be,pcm_u32le,pcm_u8,pcm_vidc,
#   qcelp,qdm2,qdmc,ra_144,ra_288,ralf,roq_dpcm,s302m,sbc,sdx2_dpcm,shorten,sipr,
#   siren,smackaudio,sol_dpcm,sonic,tak,truehd,truespeech,tta,twinvq,vmdaudio,
#   vorbis,wavarc,wavpack,wmalossless,wmapro,wmav1,wmav2,wmavoice,xan_dpcm,xma1,xma2
#
# Reference subtitle decoders / codec names:
#   ass,cc_dec,dvbsub,dvdsub,hdmv_pgs_subtitle,jacosub,microdvd,mov_text,mpl2,
#   pjs,realtext,sami,srt,ssa,stl,subrip,subviewer,subviewer1,text,ttml,vplayer,
#   webvtt,xsub
# ============================================================

# Prompt display lists are intentionally short. Full FFmpeg support is
# build-specific, so the script still loads complete runtime lists with:
#   ffmpeg -hide_banner -muxers
#   ffmpeg -hide_banner -encoders
# Keep the full runtime lists in answers["muxers"], answers["video_encoders"],
# and answers["audio_encoders"]. Only the common lists below are shown on screen.
COMMON_VIDEO_FORMATS = ["mp4", "mkv", "mov", "webm", "avi", "m4v", "ts"]
COMMON_AUDIO_FORMATS = ["mp3", "m4a", "aac", "opus", "ogg", "wav", "flac"]
COMMON_VIDEO_CODECS = ["H265", "H264", "AV1", "VP9", "MPEG4", "copy"]
COMMON_AUDIO_CODECS = ["aac", "libopus", "libmp3lame", "flac", "pcm_s16le", "copy"]
CONFIG_FILE_NAME = "config.json"
LAUNCHER_FILE_NAME = "run.ps1"
ASSET_DIR_NAME = "assets"
ICON_DIR_NAME = "icons"
CURSOR_DIR_NAME = "cursors"
DEFAULT_OUTPUT_LOCATION_TEXT = r"E:\output"


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or str(default))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)) or str(default))
    except ValueError:
        return default


EMPTY_AUDIO_MAX_BYTES = 4096
NEAR_EMPTY_AUDIO_MAX_BYTES = 1024 * 1024
NEAR_EMPTY_AUDIO_RATIO = 0.03
NEAR_EMPTY_AUDIO_MAX_KBPS = 12
PACKET_SIZE_PROBE_MAX_MB = env_int("FFMWIZ_PACKET_SCAN_MAX_MB", 64)
PACKET_SIZE_PROBE_MAX_BYTES = max(0, PACKET_SIZE_PROBE_MAX_MB) * 1024 * 1024
DUPLICATE_AUDIO_HASH_SECONDS = env_float("FFMWIZ_DUP_HASH_SECONDS", 8.0)
DUPLICATE_AUDIO_HASH_WORKERS = max(1, env_int("FFMWIZ_DUP_HASH_WORKERS", 2))
FOLDER_PROBE_WORKERS = max(1, env_int("FFMWIZ_FOLDER_PROBE_WORKERS", 4))

# Container-aware safe defaults. Full support depends on your FFmpeg build and
# muxer, so the prompt still accepts any valid runtime encoder/format name.
AUDIO_CODEC_DEFAULTS_BY_FORMAT = {
    "aac": "aac",
    "m4a": "aac",
    "mp4": "aac",
    "mkv": "aac",
    "mov": "aac",
    "mp3": "libmp3lame",
    "ogg": "libopus",
    "opus": "libopus",
    "webm": "libopus",
    "weba": "libopus",
    "flac": "flac",
    "wav": "pcm_s16le",
}

BITRATE_AUDIO_CODECS = {
    "aac",
    "ac3",
    "eac3",
    "libfdk_aac",
    "libmp3lame",
    "libopus",
    "libvorbis",
    "mp2",
    "mp3",
    "opus",
    "vorbis",
}

TEXT_SUBTITLE_CODECS = {"ass", "mov_text", "ssa", "srt", "subrip", "text", "webvtt"}
BITMAP_SUBTITLE_CODECS = {
    "dvb_subtitle",
    "dvbsub",
    "dvd_subtitle",
    "dvdsub",
    "hdmv_pgs_subtitle",
    "pgs",
    "vobsub",
    "xsub",
}
HARDSUB_BITMAP_SUBTITLE_ERROR = (
    "Bitmap subtitle streams such as PGS/VobSub/DVDSub are not supported by this HardSub mode. "
    "Choose a text subtitle stream or use an external .srt/.ass/.ssa/.vtt/.webvtt file."
)

FFMPEG_REFERENCE_FILE_NAME = "ffmwiz-ffmpeg-reference.txt"

CONFIG_TEMPLATE = """{
    "_name": "FFmWiz config",
    "_version": "2",
    "_documentation": {
        "overview": "FFmWiz is a Windows-focused interactive FFmpeg command builder. This file feeds Mode 2 (load config and ask crop only) and is also a reference document for every setting the wizard understands.",
        "how_to_edit": "Edit values inside the 'settings' object. JSON does not support /* comments */, so explanations live in the '_help' object below each section. Keys starting with an underscore are documentation only and are ignored by the parser.",
        "parser_rules": "Empty strings and missing keys fall back to interactive defaults. Use the string 'n' where supported to mean 'keep source / no change'. Booleans accept y/yes/true/1/on or n/no/false/0/off.",
        "json_syntax": "Use only double quotes for strings. No trailing commas. Backslashes in Windows paths must be escaped: C:\\\\\\\\Users\\\\\\\\Me\\\\\\\\Videos\\\\\\\\input.mkv -> in JSON that becomes \\"C:\\\\\\\\\\\\\\\\Users\\\\\\\\\\\\\\\\Me\\\\\\\\\\\\\\\\Videos\\\\\\\\\\\\\\\\input.mkv\\".",
        "windows_paths": "Forward slashes also work on Windows for FFmpeg input/output paths and are easier inside JSON. Quoted paths with spaces and Unicode characters are supported in any case.",
        "unicode": "FFmWiz uses UTF-8 throughout. Save this file as UTF-8 (no BOM is required) if you put Unicode characters in paths or titles.",
        "ffmpeg_capabilities": "Available formats, codecs, encoders, filters, etc. are build-specific. Generate a snapshot of what your installed ffmpeg.exe supports into the companion file 'ffmwiz-ffmpeg-reference.txt'. FFmWiz creates it next to this config on first run and refreshes it any time you delete it.",
        "modes": "Mode 1 = full interactive wizard (every question asked; can use the experimental Unified Video Editor on the feature/unified-editors branch). Mode 2 = read this file, then only ask the crop question. Mode 3 = stream-copy cut tool (does not read this file). Mode 4 = folder encode. Mode 5 = add audio/subtitle files to a video without re-encoding and optionally set language/title metadata for added streams. Mode 6 = write detailed ffprobe media info reports for a file or folder. Mode 7 = stream-cleanup remux for keeping selected audio/subtitle streams without re-encoding. Mode 8 = hard-sub encode for burning an internal or external subtitle into the video. Mode 9 = video speed/reverse editor. Mode 10 = audio cut/speed/reverse editor.",
        "safety": "FFmWiz never modifies the input file. The final FFmpeg command is shown before it runs and you can cancel."
    },
    "_capability_reference": {
        "_doc": "FFmpeg-supported formats, codecs, encoders, decoders, muxers, demuxers, filters, protocols, pixel formats, sample formats, and hardware accelerators all depend on the build of ffmpeg.exe installed on this machine. Do NOT assume the lists below are exhaustive.",
        "file_next_to_config": "ffmwiz-ffmpeg-reference.txt",
        "regenerate": "Delete the file or run FFmWiz; the script regenerates it from your installed FFmpeg. To inspect manually, run: ffmpeg -formats / -muxers / -demuxers / -codecs / -encoders / -decoders / -filters / -protocols / -hwaccels / -pix_fmts / -sample_fmts.",
        "container_compatibility_notes": [
            "MP4/MOV/M4V/ISMV: H.264/H.265/AV1/MPEG-4 video; AAC/AC3/EAC3/Opus(*caveat*)/ALAC audio; mov_text text subtitles only.",
            "MKV: Almost any video/audio/subtitle codec including SRT, ASS, PGS, VobSub.",
            "WebM: VP8/VP9/AV1 video; Opus/Vorbis audio; WebVTT subtitles.",
            "MP3 / M4A / WAV / FLAC / OGG / OPUS: audio-only containers. Video streams are dropped automatically by FFmWiz."
        ]
    },
    "_help": {
        "input_path": "Absolute path to the source file. Required in Mode 2. Examples: \\"C:\\\\\\\\Videos\\\\\\\\input.mkv\\", \\"D:/clips/cam01.mov\\", \\"\\\\\\\\\\\\\\\\NAS\\\\\\\\share\\\\\\\\episode.ts\\".",
        "output_path": "Either a folder (\\"E:\\\\output\\"), a full file path (\\"E:\\\\out\\\\final.mp4\\"), or a bare base name (\\"lesson6\\" -> dropped into the input folder using the chosen output_format extension). Empty uses the input folder. If the resolved output would overwrite the input, encode/re-encode adds _Encode and cut-only mode adds _cut; existing generated names receive a numeric suffix like (2).",
        "output_format": "Final container extension without leading dot. Examples: mp4, mkv, mov, webm, mp3, m4a, opus. Use the string 'n' to inherit the input extension. Container choice affects which codecs are allowed - see _capability_reference.container_compatibility_notes.",
        "video_codec": "H265 | H264 | AV1 | VP9 | MPEG4 | copy | <any encoder name from ffmpeg -encoders>. FFmWiz maps aliases to a CPU encoder and (when GPU is enabled) an NVENC encoder. Use 'copy' to stream-copy the video without re-encoding. 'copy' is incompatible with crop/fps/scale/setparams/cuts and will be auto-promoted to H265 if any filter is required.",
        "use_gpu": "y/n. y enables NVIDIA NVENC and CUDA decode/filter pipeline when the resolved encoder supports it. Falls back to CPU encoding silently when the encoder is not NVENC. Requires an FFmpeg build compiled with CUDA/NVENC support and a compatible NVIDIA GPU + driver.",
        "crop": "n (off), y (use crop_top/crop_left/crop_right/crop_bottom below), or an inline top,left,right,bottom string like '100,300,200,550'. Crop margins are pixels removed from each side, NOT x/y offsets. Mode 2 ignores this and asks crop interactively.",
        "crop_top": "Pixels removed from the top. Integer >= 0. Used only when 'crop' = y.",
        "crop_left": "Pixels removed from the left. Integer >= 0.",
        "crop_right": "Pixels removed from the right. Integer >= 0.",
        "crop_bottom": "Pixels removed from the bottom. Integer >= 0.",
        "video_bitrate_kbps": "Target average video bitrate in kbps. Examples: 400, 800, 1500, 3500, 8000. Use 'n' to keep the detected source bitrate. Interactive prompts warn before accepting a target above the detected source bitrate.",
        "video_bitrate_mode": "quality_vbr or strict_size. quality_vbr uses -b:v Xk -maxrate:v 2Xk -bufsize:v 4Xk. strict_size uses -b:v Xk -maxrate:v Xk -bufsize:v 2Xk.",
        "resolution": "Output scale target. Presets/plain numbers like 480p or 480 preserve aspect ratio using closest-edge scaling against the standard preset box. Use w720/720w for explicit width, h480/480h for explicit height, WIDTHxHEIGHT for a preserve-aspect target box, and stretch:WIDTHxHEIGHT only when intentional distortion is wanted. Use 'n' to keep source/cropped size. SAR is forced to 1 by default.",
        "fps": "Output frames-per-second as an integer. Use 'n' to keep the source rate. Examples: 24, 25, 30, 50, 60. Interactive prompts warn before accepting an FPS above the detected source FPS. Float rates (e.g. 23.976) are not exposed here; if you need fractional rates, prefer the interactive wizard or edit the FFmpeg command before running.",
        "audio_tracks": "Selection for which audio streams to keep. Accepts: '0' or '0,1,2' (stream indices among audio streams), 'all', 'd' (drop confirmed duplicates), 'e' (drop empty / near-empty), 'de' (both). Empty defaults to 'de'.",
        "audio_codec": "aac | libopus | libmp3lame | flac | pcm_s16le | copy | <any encoder name from ffmpeg -encoders>. Container compatibility is enforced: WebM forces libopus; pcm_*/flac ignore bitrate; 'copy' skips re-encoding.",
        "audio_bitrate_kbps": "Target audio bitrate per stream in kbps. Used only for bitrate-based codecs (aac/libopus/libmp3lame/etc.). Use 'n' to keep the source bitrate. Interactive prompts warn before accepting a target above the detected selected source audio bitrate. Common values: 64, 96, 128, 160, 192, 256, 320.",
        "subtitle_tracks": "Selection for which subtitle streams to keep. Same syntax as audio_tracks plus 'none' / 'clear' / 'delete' to drop all subtitles. MP4/MOV outputs convert text subtitles to mov_text and drop non-text (PGS, VobSub).",
        "detect_duplicate_audio": "y/n. When y, FFmWiz uses stream metadata plus exact packet sizes when needed to flag empty/near-empty tracks. Likely duplicate tracks are prechecked with short sampled hashes, then confirmed with a full audio hash. Used by the 'd' / 'e' / 'de' shortcuts.",
        "logging_enabled": "y/n. Logging is enabled by default and writes dated UTF-8 logs into the Logs folder next to FFmWiz.py. Set to n only when you intentionally want no log file for future runs.",
        "log_retention_days": "Optional integer. 0 keeps logs forever. Any positive value deletes FFmWiz log files older than that many days when logging starts."
    },
    "settings": {
        "input_path": "",
        "output_path": "",
        "output_format": "n",
        "video_codec": "H265",
        "use_gpu": "y",
        "crop": "n",
        "crop_top": 0,
        "crop_left": 0,
        "crop_right": 0,
        "crop_bottom": 0,
        "video_bitrate_kbps": "n",
        "video_bitrate_mode": "quality_vbr",
        "resolution": "n",
        "fps": "n",
        "audio_tracks": "de",
        "audio_codec": "aac",
        "audio_bitrate_kbps": "n",
        "subtitle_tracks": "none",
        "detect_duplicate_audio": "y",
        "logging_enabled": "y",
        "log_retention_days": 0
    },
    "_examples": {
        "fast_stream_copy_same_container": {
            "_doc": "Stream-copy the source video/audio without re-encoding while keeping the original container. Pure copy/remux workflows should not change the output extension.",
            "settings": {"output_format": "n", "video_codec": "copy", "use_gpu": "n", "crop": "n", "audio_codec": "copy", "subtitle_tracks": "none"}
        },
        "h264_mp4_1080p_cpu": {
            "_doc": "Classic 1080p H.264 MP4, CPU encode, AAC stereo audio at 160 kbps.",
            "settings": {"output_format": "mp4", "video_codec": "H264", "use_gpu": "n", "resolution": "1080p", "fps": "n", "video_bitrate_kbps": 6000, "audio_codec": "aac", "audio_bitrate_kbps": 160, "subtitle_tracks": "none"}
        },
        "h265_mp4_nvenc": {
            "_doc": "HEVC NVENC for fast GPU-accelerated 1080p encodes; tag:v hvc1 is added automatically for Apple compatibility.",
            "settings": {"output_format": "mp4", "video_codec": "H265", "use_gpu": "y", "resolution": "1080p", "video_bitrate_kbps": 4500, "audio_codec": "aac", "audio_bitrate_kbps": 160}
        },
        "av1_webm": {
            "_doc": "AV1 in WebM with Opus audio. AV1 NVENC is preferred when use_gpu=y and the build supports av1_nvenc.",
            "settings": {"output_format": "webm", "video_codec": "AV1", "use_gpu": "y", "video_bitrate_kbps": 2500, "audio_codec": "libopus", "audio_bitrate_kbps": 96}
        },
        "audio_only_aac_m4a": {
            "_doc": "Extract first audio track as AAC in an M4A container.",
            "settings": {"output_format": "m4a", "audio_tracks": "0", "audio_codec": "aac", "audio_bitrate_kbps": 192, "subtitle_tracks": "none"}
        },
        "audio_only_flac": {
            "_doc": "Lossless FLAC audio. audio_bitrate_kbps is ignored for FLAC.",
            "settings": {"output_format": "flac", "audio_tracks": "all", "audio_codec": "flac", "subtitle_tracks": "none"}
        },
        "drop_duplicate_and_empty_audio": {
            "_doc": "Keep all audio but drop confirmed duplicates and empty/near-empty tracks before encoding.",
            "settings": {"audio_tracks": "de", "detect_duplicate_audio": "y"}
        },
        "crop_and_scale_720p": {
            "_doc": "Crop a letterboxed source and scale down to 720p with H.265 NVENC.",
            "settings": {"video_codec": "H265", "use_gpu": "y", "crop": "y", "crop_top": 132, "crop_left": 0, "crop_right": 0, "crop_bottom": 132, "resolution": "720p", "video_bitrate_kbps": 3000, "audio_codec": "aac", "audio_bitrate_kbps": 128}
        }
    },
    "_glossary": {
        "stream_copy_vs_reencode": "Stream copy (-c copy) skips decoding/encoding entirely. It is keyframe-bound for video, so cut points snap to nearby keyframes. Re-encoding is slower and lossy per pass but is frame-accurate and lets filters run.",
        "crf_vs_bitrate": "CRF (Constant Rate Factor) targets a perceptual quality level (libx264/libx265 default ~ 23; smaller = higher quality). Bitrate mode (-b:v + -maxrate + -bufsize) targets a file size. FFmWiz currently uses bitrate mode with NVENC's VBR rc.",
        "preset_tune_profile": "preset = encoder speed/quality tradeoff (NVENC: p1..p7; libx264/libx265: ultrafast..veryslow). tune = content hint (NVENC: hq/ll/ull/lossless). profile = stream profile (HEVC: main / main10 / rext).",
        "gop": "GOP = Group of Pictures; the keyframe interval. Larger GOP = better compression but slower seeking. Default GOP is set by the encoder.",
        "movflags_faststart": "Moves the MP4 moov atom to the start of the file so streaming/progressive playback can begin without downloading the whole file. FFmWiz applies +faststart to MP4/MOV outputs by default.",
        "tag_hvc1_vs_hev1": "Apple devices and some browsers require the hvc1 tag instead of hev1 for HEVC in MP4. FFmWiz applies -tag:v hvc1 automatically for HEVC -> MP4-like outputs.",
        "color_range_tv_vs_pc": "tv = limited range 16..235 (YUV broadcast). pc = full range 0..255. FFmWiz defaults to tv for compatibility.",
        "yuv420p_vs_nv12": "yuv420p is the most compatible CPU pixel format. NV12 is the typical NVENC input. FFmWiz handles conversion automatically based on the pipeline."
    }
}
"""


AUDIO_ONLY_EXTS = {
    "aac",
    "ac3",
    "aiff",
    "alac",
    "amr",
    "ape",
    "au",
    "dts",
    "eac3",
    "flac",
    "m4a",
    "mka",
    "mp2",
    "mp3",
    "oga",
    "ogg",
    "opus",
    "wav",
    "weba",
    "wma",
}

FOLDER_VIDEO_EXTS = {
    "3g2",
    "3gp",
    "asf",
    "avi",
    "divx",
    "dv",
    "f4v",
    "flv",
    "hevc",
    "m2ts",
    "m2v",
    "m4v",
    "mjpeg",
    "mkv",
    "mov",
    "mp4",
    "mpeg",
    "mpg",
    "mts",
    "mxf",
    "ogm",
    "ogv",
    "rm",
    "rmvb",
    "ts",
    "vob",
    "webm",
    "wmv",
    "y4m",
}
FOLDER_MEDIA_EXTS = FOLDER_VIDEO_EXTS | AUDIO_ONLY_EXTS | set(COMMON_VIDEO_FORMATS) | set(COMMON_AUDIO_FORMATS)

MP4_LIKE_EXTS = {"mp4", "m4a", "m4v", "mov", "ismv"}
ADD_FILES_OUTPUT_SUFFIX = "_with_tracks"
MEDIA_REPORTS_DIR_NAME = "MediaReports"
MUX_CLEANUP_VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".webm", ".mov", ".avi"}
HARDSUB_OUTPUT_SUFFIX = "_HardSub"
HARDSUB_SUBTITLE_EXTS = {".ass", ".ssa", ".srt", ".vtt", ".webvtt"}
HARDSUB_QUALITY_PRESETS = {
    "near-lossless": {"cpu": 14, "cpu_hevc": 16, "nvenc": 13},
    "high quality": {"cpu": 17, "cpu_hevc": 18, "nvenc": 16},
    "balanced": {"cpu": 20, "cpu_hevc": 22, "nvenc": 20},
}

RESOLUTION_PRESETS = {
    "144p": (256, 144),
    "240p": (426, 240),
    "360p": (640, 360),
    # Preset shorthands are standard target boxes. Scaling preserves the
    # cropped source aspect ratio by pinning the closest matching edge.
    "480p": (720, 480),
    "576p": (720, 576),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "2160p": (3840, 2160),
    "4320p": (7680, 4320),
}

VIDEO_CODEC_ALIASES = {
    "h265": {"cpu": "libx265", "gpu": "hevc_nvenc", "tag": "hvc1", "profile": NVENC_HEVC_PROFILE},
    "hevc": {"cpu": "libx265", "gpu": "hevc_nvenc", "tag": "hvc1", "profile": NVENC_HEVC_PROFILE},
    "h264": {"cpu": "libx264", "gpu": "h264_nvenc", "tag": "avc1", "profile": None},
    "avc": {"cpu": "libx264", "gpu": "h264_nvenc", "tag": "avc1", "profile": None},
    "av1": {"cpu": "libaom-av1", "gpu": "av1_nvenc", "tag": None, "profile": None},
    "vp9": {"cpu": "libvpx-vp9", "gpu": None, "tag": None, "profile": None},
    "mpeg4": {"cpu": "mpeg4", "gpu": None, "tag": "mp4v", "profile": None},
}

CUDA_CUVID_DECODER_BY_CODEC = {
    "av1": "av1_cuvid",
    "avc": "h264_cuvid",
    "avc1": "h264_cuvid",
    "h264": "h264_cuvid",
    "h265": "hevc_cuvid",
    "hevc": "hevc_cuvid",
    "mjpeg": "mjpeg_cuvid",
    "mpeg2": "mpeg2_cuvid",
    "mpeg2video": "mpeg2_cuvid",
    "vc1": "vc1_cuvid",
    "vp8": "vp8_cuvid",
    "vp9": "vp9_cuvid",
}


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[38;5;117m"
    MAGENTA = "\033[38;5;219m"
    CYAN = "\033[38;5;123m"
    WHITE = "\033[97m"
    DIM = "\033[38;5;250m"
    GRAY = "\033[38;5;252m"
    ORANGE = "\033[38;5;222m"
    LIGHT_BLUE = "\033[38;5;117m"
    LIGHT_YELLOW = "\033[38;5;229m"
    NOTE_YELLOW = "\033[38;5;227m"
    HINT_YELLOW = "\033[38;5;221m"
    AQUA = "\033[38;5;159m"
    PINK = "\033[38;5;218m"
    LIME = "\033[38;5;118m"
    KEEP_VALUE = "\033[38;5;207m"
    RES_NUMBERS = "\033[38;5;165m"
    RES_TARGET = "\033[38;5;204m"
    RES_EXACT = "\033[38;5;141m"
    AUDIO_ALL = "\033[38;5;120m"
    AUDIO_DROP_DUP = "\033[38;5;208m"
    AUDIO_DROP_EMPTY = "\033[38;5;198m"
    AUDIO_DROP_BOTH = "\033[38;5;99m"
    AUDIO_TRACK_NOTE = "\033[38;5;87m"
    FINAL_COMMAND_LABEL = "\033[38;5;75m"
    FINAL_COMMAND_TEXT = "\033[38;5;153m"
    SUGGESTION = "\033[38;5;190m"
    BACK_PROMPT = "\033[38;5;166m"
    EXIT_PROMPT = "\033[38;5;32m"
    NEAR_EMPTY = "\033[38;5;172m"
    ZERO_INLINE = "\033[38;5;177m"
    PROGRESS_PERCENT = "\033[38;5;46m"
    PROGRESS_TIME = "\033[38;5;51m"
    PROGRESS_FPS = "\033[38;5;226m"
    PROGRESS_Q = "\033[38;5;202m"
    PROGRESS_SPEED = "\033[38;5;171m"
    PROGRESS_SIZE = "\033[38;5;119m"
    PROGRESS_BITRATE = "\033[38;5;39m"
    PROGRESS_ELAPSED = "\033[38;5;180m"
    PROGRESS_ETA_LABEL = "\033[38;2;255;78;178m"
    PROGRESS_ETA_VALUE = "\033[38;2;255;132;206m"
    COLOR_RANGE_VALUE = "\033[38;2;90;210;255m"
    WIZARD_TITLE = "\033[38;2;255;50;115m"


USE_COLOR = os.environ.get("NO_COLOR") is None


class Back(Exception):
    pass


class ExitWizard(Exception):
    pass


class RetryAdditionalFile(Exception):
    pass


class FFprobeError(RuntimeError):
    pass


@dataclass
class Step:
    name: str
    applicable: Callable[[dict[str, Any]], bool]
    run: Callable[[dict[str, Any]], None]


def paint(text: str, color_code: str) -> str:
    if not USE_COLOR:
        return text
    return f"{color_code}{text}{Color.RESET}"


def error(message: str) -> None:
    print(paint(message, Color.RED))
    try:
        log_error(message)
    except NameError:
        pass


def note(message: str) -> None:
    print(paint(message, Color.NOTE_YELLOW))
    try:
        log_info(message)
    except NameError:
        pass


def option_list(items: list[str]) -> str:
    return paint(",".join(items), Color.LIGHT_BLUE)


def example_text(text: str) -> str:
    return paint(text, Color.LIGHT_BLUE)


def keep_value_text(text: str) -> str:
    return paint(text, Color.KEEP_VALUE)


def suggestion_text(text: str) -> str:
    return paint(text, Color.SUGGESTION)


def colored_audio_track_hint() -> str:
    return (
        f"{paint('n/all=all', Color.AUDIO_ALL)}; "
        f"{paint('d=drop confirmed duplicates', Color.AUDIO_DROP_DUP)}; "
        f"{paint('e=drop empty/near-empty', Color.AUDIO_DROP_EMPTY)}; "
        f"{paint('de=both', Color.AUDIO_DROP_BOTH)}; "
        f"{paint('track number 0 is the first audio track', Color.AUDIO_TRACK_NOTE)}"
    )


def colored_hardsub_quality_options() -> str:
    return (
        f"{paint('1=near-lossless (closest to source)', Color.LIME)}; "
        f"{paint('2=high quality (smaller)', Color.CYAN)}; "
        f"{paint('3=balanced (more compression)', Color.ORANGE)}; "
        f"{paint('4=custom CRF/CQ', Color.PINK)}"
    )


def colored_hardsub_audio_options() -> str:
    return (
        f"{paint('1=copy all audio tracks', Color.LIME)}; "
        f"{paint('2=choose audio tracks to copy', Color.CYAN)}; "
        f"{paint('3=no audio', Color.RED)}"
    )


PROGRESS_COLORS: dict[str, str] = {
    "percent": Color.PROGRESS_PERCENT,
    "time": Color.PROGRESS_TIME,
    "total": Color.GRAY,
    "fps": Color.PROGRESS_FPS,
    "q": Color.PROGRESS_Q,
    "speed": Color.PROGRESS_SPEED,
    "size": Color.PROGRESS_SIZE,
    "bitrate": Color.PROGRESS_BITRATE,
    "elapsed": Color.PROGRESS_ELAPSED,
    "eta_label": Color.PROGRESS_ETA_LABEL,
    "eta_value": Color.PROGRESS_ETA_VALUE,
    "separator": Color.DIM,
}


def back_text(text: str = "back=0, quit=exit") -> str:
    parts = []
    for part in text.split(", "):
        lowered = part.lower()
        if "back" in lowered:
            parts.append(paint(part, Color.BACK_PROMPT))
        elif "exit" in lowered:
            parts.append(paint(part, Color.EXIT_PROMPT))
        else:
            parts.append(paint(part, Color.WHITE))
    return "{" + ", ".join(parts) + "}"


def field_text(name: str, value: Any, value_color: str = Color.WHITE) -> str:
    return f"{paint(name + ':', Color.GRAY)} {paint(str(value), value_color)}"


def format_elapsed(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_progress_duration(seconds: float | None) -> str:
    if seconds is None:
        return "calculating"
    return format_elapsed(seconds)


def format_progress_clock(seconds: float | None) -> str:
    total = max(0, int(round(seconds or 0)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_progress_elapsed_dot(seconds: float | None) -> str:
    total = max(0, int(round(seconds or 0)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}.{secs:02d}"


# ------------------------------------------------------------------
# Time / cut-range helpers used by the new "Cut video only with copy"
# mode and by the re-encode cut path inside the interactive wizard.
# ------------------------------------------------------------------

HMSF_PATTERN = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*:\s*(\d+)\s*:\s*(\d+)\s*$")


def get_video_fps(answers: dict[str, Any], default: float = 25.0) -> float:
    """Detect the input video FPS. Prefers avg_frame_rate, falls back to r_frame_rate."""
    streams = answers.get("video_streams") or []
    if not streams:
        return default
    stream = streams[0]
    for key in ("avg_frame_rate", "r_frame_rate"):
        rate = rational_to_float(stream.get(key))
        if rate and rate > 0:
            return rate
    return default


def parse_hmsf_time(value: str, fps: float) -> float:
    """Parse a h:m:s:frame string into seconds.

    Small frame overflow is normalized: if frame >= rounded fps, the extra time
    rolls over into seconds. Validation:
      hours   >= 0
      minutes 0-59
      seconds 0-59
      frame   >= 0 and below 10 seconds worth of frames
    """
    if value is None or not str(value).strip():
        raise ValueError("Time is empty. Expected h:m:s:frame, e.g. 00:01:30:12")
    match = HMSF_PATTERN.match(str(value))
    if not match:
        raise ValueError(f"Invalid time format. Expected h:m:s:frame, got {value!r}")
    hours, minutes, seconds, frame = (int(group) for group in match.groups())
    if minutes >= 60:
        raise ValueError(f"Minutes must be 0-59, got {minutes}")
    if seconds >= 60:
        raise ValueError(f"Seconds must be 0-59, got {seconds}")
    if fps <= 0:
        fps = 25.0
    fps_int = max(1, round(fps))
    frame_limit = fps_int * 10
    if frame >= frame_limit:
        raise ValueError(
            f"Frame value is too large for {fps_int} fps: got {frame}, "
            f"maximum accepted overflow is {frame_limit - 1}"
        )
    total = hours * 3600.0 + minutes * 60.0 + seconds + frame / fps
    return total


def seconds_to_hmsf(seconds: float, fps: float) -> str:
    """Format seconds as h:m:s:frame using the supplied FPS."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    if fps <= 0:
        fps = 25.0
    fps_int = max(1, round(fps))
    total_frames = int(round(float(seconds) * fps))
    frame = total_frames % fps_int
    whole_seconds = total_frames // fps_int
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frame:02d}"


def seconds_to_ffmpeg_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm suitable for FFmpeg -ss / -to."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    total_ms = int(round(float(seconds) * 1000))
    hours, remainder = divmod(total_ms, 3600 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    secs = remainder / 1000.0
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def normalize_cut_ranges(
    ranges: list[tuple[float, float]],
    duration: float,
) -> list[tuple[float, float]]:
    """Clean a list of (start, end) ranges: clamp to [0, duration], drop empty
    or inverted ones, sort, and merge overlapping/touching intervals."""
    duration = max(0.0, float(duration or 0.0))
    cleaned: list[tuple[float, float]] = []
    for start, end in ranges or []:
        try:
            start_value = max(0.0, float(start))
            end_value = float(end)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            start_value = min(start_value, duration)
            end_value = min(end_value, duration)
        if end_value <= start_value:
            continue
        cleaned.append((start_value, end_value))
    cleaned.sort()
    merged: list[tuple[float, float]] = []
    for start, end in cleaned:
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def invert_cut_ranges_to_keep_ranges(
    remove_ranges: list[tuple[float, float]],
    duration: float,
) -> list[tuple[float, float]]:
    """Convert a list of remove ranges into the equivalent keep ranges for the
    file duration. Useful when the user picks 'Remove ...' modes."""
    duration = max(0.0, float(duration or 0.0))
    remove = normalize_cut_ranges(remove_ranges, duration)
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in remove:
        if start > cursor:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        keep.append((cursor, duration))
    return keep


def total_keep_duration(keep_ranges: list[tuple[float, float]]) -> float:
    return sum(max(0.0, end - start) for start, end in keep_ranges)


# ------------------------------------------------------------------
# Speed / reverse helpers.
# ------------------------------------------------------------------


def clamp_speed_factor(value: Any) -> float:
    try:
        factor = float(value)
    except (TypeError, ValueError):
        raise ValueError("Speed must be a number.")
    if factor <= 0:
        raise ValueError("Speed must be greater than zero.")
    if factor < MIN_SPEED_FACTOR or factor > MAX_SPEED_FACTOR:
        raise ValueError(
            f"Speed must be between {MIN_SPEED_FACTOR:g}x and {MAX_SPEED_FACTOR:g}x."
        )
    return factor


def parse_speed_factor(value: str) -> float:
    text = str(value or "").strip().lower()
    if text.endswith("%"):
        return clamp_speed_factor(float(text[:-1].strip()) / 100.0)
    if text.endswith("x"):
        text = text[:-1].strip()
    return clamp_speed_factor(text)


def ffmpeg_float(value: float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


def atempo_filter_chain(speed: float) -> str:
    """Build an atempo chain with each stage kept in FFmpeg's safe 0.5..2.0
    range. This avoids the artifacts/skipped-sample behavior of very large
    single atempo values."""
    remaining = clamp_speed_factor(speed)
    stages: list[float] = []
    while remaining > 2.0 + 1e-9:
        stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        stages.append(0.5)
        remaining /= 0.5
    stages.append(remaining)
    return ",".join(f"atempo={ffmpeg_float(stage)}" for stage in stages)


def build_video_speed_filter(speed: float, reverse: bool) -> str:
    speed = clamp_speed_factor(speed)
    filters: list[str] = []
    if reverse:
        filters.append("reverse")
    filters.append(f"setpts=(PTS-STARTPTS)/{ffmpeg_float(speed)}")
    return ",".join(filters)


def build_audio_speed_filter(speed: float, reverse: bool) -> str:
    speed = clamp_speed_factor(speed)
    filters: list[str] = []
    if reverse:
        filters.append("areverse")
    filters.append("asetpts=PTS-STARTPTS")
    filters.append(atempo_filter_chain(speed))
    return ",".join(filters)


def video_speed_transform_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("video_speed_enabled"))


def audio_speed_transform_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("audio_speed_enabled") or answers.get("audio_speed_from_video"))


def audio_cut_transform_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("audio_cut_keep_ranges"))


def audio_transform_enabled(answers: dict[str, Any]) -> bool:
    return audio_speed_transform_enabled(answers) or audio_cut_transform_enabled(answers)


def encode_video_speed_factor(answers: dict[str, Any]) -> float:
    return clamp_speed_factor(answers.get("video_speed_factor", answers.get("speed_factor", DEFAULT_SPEED_FACTOR)))


def encode_audio_speed_factor(answers: dict[str, Any]) -> float:
    if answers.get("audio_speed_enabled"):
        return clamp_speed_factor(answers.get("audio_speed_factor", DEFAULT_SPEED_FACTOR))
    if answers.get("audio_speed_from_video"):
        return encode_video_speed_factor(answers)
    return DEFAULT_SPEED_FACTOR


def encode_audio_reverse_enabled(answers: dict[str, Any]) -> bool:
    if answers.get("audio_speed_enabled"):
        return bool(answers.get("reverse_audio"))
    if answers.get("audio_speed_from_video"):
        return bool(answers.get("reverse_video"))
    return False


def build_encode_audio_speed_filter(answers: dict[str, Any]) -> str:
    return build_audio_speed_filter(encode_audio_speed_factor(answers), encode_audio_reverse_enabled(answers))


def default_audio_output_ext(input_path: Path) -> str:
    ext = input_path.suffix.lstrip(".").lower()
    if ext in {"mp3", "m4a", "aac", "opus", "ogg", "wav", "flac"}:
        return ext
    return "m4a"


def resolve_audio_tool_output_ext(answers: dict[str, Any]) -> str:
    output_location = answers.get("output_location")
    if isinstance(output_location, Path) and output_location.suffix:
        requested = output_location.suffix.lstrip(".").lower()
        if requested in {"mp3", "m4a", "aac", "opus", "ogg", "wav", "flac"}:
            return requested
    input_path = answers.get("input_path")
    if isinstance(input_path, Path):
        return default_audio_output_ext(input_path)
    return "m4a"


def audio_tool_encode_options(output_ext: str, bitrate_kbps: int = DEFAULT_SPEED_AUDIO_BITRATE_KBPS) -> list[str]:
    ext = str(output_ext or "").lower().lstrip(".")
    if ext == "mp3":
        options = ["-c:a", "libmp3lame", "-b:a", f"{int(bitrate_kbps)}k"]
    elif ext in {"opus", "ogg"}:
        options = ["-c:a", "libopus", "-b:a", f"{int(bitrate_kbps)}k"]
    elif ext == "wav":
        options = ["-c:a", "pcm_s16le"]
    elif ext == "flac":
        options = ["-c:a", "flac"]
    else:
        options = ["-c:a", DEFAULT_AUDIO_CODEC, "-b:a", f"{int(bitrate_kbps)}k"]
    if AUDIO_CHANNELS:
        options.extend(["-ac", str(AUDIO_CHANNELS)])
    return options


def speed_suffix(speed: float, reverse: bool) -> str:
    percent = int(round(speed * 100))
    return f"_Speed{percent}" + ("_Reverse" if reverse else "")


def split_ranges_for_reverse_segments(
    ranges: list[tuple[float, float]],
    duration: float,
    segment_seconds: float = REVERSE_SEGMENT_SECONDS,
) -> list[tuple[float, float]]:
    duration = max(0.0, float(duration or 0.0))
    segment_seconds = max(1.0, float(segment_seconds or REVERSE_SEGMENT_SECONDS))
    source_ranges = normalize_cut_ranges(ranges, duration) if ranges else []
    if not source_ranges and duration > 0:
        source_ranges = [(0.0, duration)]
    chunks: list[tuple[float, float]] = []
    for start, end in source_ranges:
        cursor = max(0.0, start)
        end = min(duration, end) if duration > 0 else end
        while cursor < end - 1e-6:
            next_end = min(end, cursor + segment_seconds)
            if next_end > cursor:
                chunks.append((cursor, next_end))
            cursor = next_end
    return chunks


def write_concat_list(paths: list[Path], concat_list: Path) -> None:
    with concat_list.open("w", encoding="utf-8") as handle:
        for path in paths:
            escaped = path.as_posix().replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")


# ------------------------------------------------------------------
# Shared GUI helpers used by both the Crop Editor and Cut Editor:
#  - _apply_dark_title_bar: enable Windows DWM immersive dark mode
#    on the window's native frame so the system title bar stops
#    looking bright/system-default.
#  - _PreviewScheduler: debounced, asynchronous, cached frame
#    extraction. Keeps the UI thread responsive while scrubbing.
# ------------------------------------------------------------------


def _apply_dark_title_bar(window: Any) -> None:
    """Switch the native window frame to dark mode on Windows 10/11.

    No-op on other platforms. Safe to call multiple times. Failures are
    swallowed so unsupported Windows versions or non-DWM environments do
    not break the GUI.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        enabled = ctypes.c_int(1)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE on Windows 11 / late 10.
        # 19 = legacy attribute for early Windows 10 builds.
        for attribute in (20, 19):
            attr_result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                attribute,
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            )
            if attr_result == 0:
                break
    except Exception:
        return


# ============================================================
# Shared professional dark UI palette + helpers used by both
# the Cut Editor and the Crop Editor GUIs. Colors inspired by
# common Premiere Pro style guides: very dark workspace, soft
# panel surfaces, a single accent (blue) for primary actions,
# and high-contrast text on top.
# ============================================================


class _UIPalette:
    BG = "#0e1217"             # main app background
    PANEL = "#161a22"          # toolbar / header band
    PANEL_HI = "#1b2030"
    SURFACE = "#1d232d"        # button / widget surface
    SURFACE_HOVER = "#252c39"
    SURFACE_PRESSED = "#2e3a52"
    SURFACE_DIS = "#161a22"
    BORDER = "#2e3a4f"
    BORDER_SOFT = "#1f2937"
    TIMELINE_BG = "#0f1422"
    TIMELINE_TRACK = "#1c2336"
    TIMELINE_TICK = "#5b6f91"
    TIMELINE_TICK_HI = "#c2cbe1"
    ACCENT = "#5b9eff"         # primary action accent
    ACCENT_STRONG = "#7ab0ff"
    ACCENT_DARK = "#1f3a66"
    ACCENT_RED = "#ff6f6f"
    ACCENT_RED_DK = "#c44a4a"
    ACCENT_GREEN = "#7be07b"
    ACCENT_YELLOW = "#f5d66a"
    ACCENT_ORANGE = "#f5b341"
    PLAYHEAD = "#ffffff"
    TEXT = "#f5f7fb"
    TEXT_DIM = "#c2cbe1"
    TEXT_MUTE = "#7c8aa6"
    TEXT_ON_ACCENT = "#0e1217"


def _apply_app_ttk_theme(root: Any) -> None:
    """Apply the unified FFmWiz dark ttk theme to the given Tk root."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return
    palette = _UIPalette
    try:
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=palette.BG)
        style.configure("Panel.TFrame", background=palette.PANEL)
        style.configure("Surface.TFrame", background=palette.SURFACE)
        style.configure("TLabel", background=palette.BG, foreground=palette.TEXT)
        style.configure("Panel.TLabel", background=palette.PANEL, foreground=palette.TEXT)
        style.configure("Dim.TLabel", background=palette.BG, foreground=palette.TEXT_DIM)
        style.configure("Muted.TLabel", background=palette.BG, foreground=palette.TEXT_MUTE)
        style.configure(
            "Header.TLabel",
            background=palette.PANEL,
            foreground=palette.TEXT,
            font=("Segoe UI Semibold", 12),
        )
        style.configure(
            "Title.TLabel",
            background=palette.PANEL,
            foreground=palette.ACCENT_STRONG,
            font=("Segoe UI Semibold", 13),
        )
        style.configure(
            "TButton",
            background=palette.SURFACE,
            foreground=palette.TEXT,
            bordercolor=palette.BORDER,
            focusthickness=0,
            padding=(12, 7),
            font=("Segoe UI", 9),
        )
        style.map(
            "TButton",
            background=[
                ("active", palette.SURFACE_HOVER),
                ("pressed", palette.SURFACE_PRESSED),
                ("disabled", palette.SURFACE_DIS),
            ],
            foreground=[("disabled", palette.TEXT_MUTE)],
        )
        # Primary action button (used for Confirm / Apply / Play).
        style.configure(
            "Accent.TButton",
            background=palette.ACCENT,
            foreground=palette.TEXT_ON_ACCENT,
            bordercolor=palette.ACCENT_STRONG,
            padding=(12, 7),
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", palette.ACCENT_STRONG),
                ("pressed", palette.ACCENT_STRONG),
                ("disabled", palette.SURFACE_DIS),
            ],
            foreground=[("disabled", palette.TEXT_MUTE)],
        )
        style.configure(
            "Danger.TButton",
            background=palette.SURFACE,
            foreground=palette.ACCENT_RED,
            padding=(12, 7),
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#2a1f24"), ("pressed", "#3a1f24")],
        )
        style.configure(
            "Tool.TButton",
            background=palette.SURFACE,
            foreground=palette.TEXT,
            padding=(10, 6),
        )
        style.configure(
            "ToolActive.TButton",
            background=palette.ACCENT_DARK,
            foreground=palette.ACCENT_STRONG,
            padding=(10, 6),
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "ToolActive.TButton",
            background=[("active", palette.ACCENT_DARK), ("pressed", palette.ACCENT_DARK)],
        )
        style.configure(
            "TScale",
            background=palette.BG,
            troughcolor=palette.TIMELINE_TRACK,
            bordercolor=palette.BORDER,
        )
        style.configure("TSeparator", background=palette.BORDER)
        style.configure(
            "TLabelframe",
            background=palette.BG,
            foreground=palette.TEXT_DIM,
            bordercolor=palette.BORDER,
        )
        style.configure(
            "TLabelframe.Label",
            background=palette.BG,
            foreground=palette.TEXT_DIM,
        )
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=palette.SURFACE,
            troughcolor=palette.PANEL,
            bordercolor=palette.BG,
            arrowcolor=palette.ACCENT_YELLOW,
            darkcolor=palette.SURFACE,
            lightcolor=palette.SURFACE_HOVER,
        )
        style.configure(
            "Dark.Horizontal.TScrollbar",
            background=palette.SURFACE,
            troughcolor=palette.PANEL,
            bordercolor=palette.BG,
            arrowcolor=palette.ACCENT_YELLOW,
            darkcolor=palette.SURFACE,
            lightcolor=palette.SURFACE_HOVER,
        )
        style.map(
            "Dark.Vertical.TScrollbar",
            background=[("active", palette.SURFACE_HOVER), ("pressed", palette.SURFACE_PRESSED)],
        )
        style.map(
            "Dark.Horizontal.TScrollbar",
            background=[("active", palette.SURFACE_HOVER), ("pressed", palette.SURFACE_PRESSED)],
        )
    except Exception:
        pass


def _make_icon_loader(root: Any) -> Callable[[str], Any]:
    """Return a cached PNG-icon loader bound to a Tk root.

    The loader reads PNGs from `assets/icons/<name>.png` and caches the
    resulting tk.PhotoImage. Returns None if the icon does not exist.
    Both GUIs share the same on-disk icon assets so they look the same.
    """
    cache: dict[str, Any] = {}

    def loader(name: str) -> Any | None:
        if name in cache:
            return cache[name]
        path = asset_path(ICON_DIR_NAME, f"{name}.png")
        if not path.exists():
            cache[name] = None
            return None
        try:
            import tkinter as tk

            cache[name] = tk.PhotoImage(file=str(path), master=root)
        except Exception:
            cache[name] = None
        return cache[name]

    return loader


def _apply_tk_window_icon(root: Any) -> None:
    """Set a project-local icon on Tk roots for window chrome/taskbar."""
    png_path = asset_path(ICON_DIR_NAME, "ffmwiz_app.png")
    ico_path = asset_path(ICON_DIR_NAME, "ffmwiz_app.ico")
    try:
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FFmWiz.GUI")
                log_debug("Tk Windows AppUserModelID set: FFmWiz.GUI")
            except Exception as exc:
                log_debug(f"Could not set Tk AppUserModelID: {exc}")
        if ico_path.exists() and os.name == "nt":
            try:
                root.iconbitmap(default=str(ico_path))
                log_debug(f"Tk window iconbitmap set: {ico_path}")
            except Exception as exc:
                log_debug(f"Tk iconbitmap failed for {ico_path}: {exc}")
        if png_path.exists():
            import tkinter as tk
            icon = tk.PhotoImage(file=str(png_path), master=root)
            root.iconphoto(True, icon)
            root._ffmwiz_icon_ref = icon
            log_debug(f"Tk window iconphoto set: {png_path}")
        elif not ico_path.exists():
            log_debug("No Tk app icon asset found (ffmwiz_app.png/.ico).")
    except Exception as exc:
        log_debug(f"Could not set Tk window icon: {exc}")


# ============================================================
# Layout-independent keyboard shortcuts.
#
# Tkinter's bind("<KeyPress-h>") matches by keysym, which depends on
# the active keyboard layout. When the user is on a Persian or other
# non-Latin layout, the same physical key produces a different keysym
# and the binding silently fails to fire.
#
# To make shortcuts work regardless of layout we bind a single
# <KeyPress> handler that matches BOTH:
#   - event.keycode (the Windows virtual-key code on Windows; the
#     X11 hardware keycode on Linux; layout-independent on both),
#   - event.keysym (lower-cased; layout-dependent but still useful
#     as a portable fallback).
#
# WIN_VK_BY_NAME maps friendly names -> Windows VK codes.
# ============================================================

WIN_VK_BY_NAME: dict[str, int] = {
    "space": 0x20, "return": 0x0D, "enter": 0x0D, "escape": 0x1B,
    "delete": 0x2E, "back": 0x08, "tab": 0x09,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "plus": 0xBB, "equal": 0xBB, "minus": 0xBD,
    "kp_add": 0x6B, "kp_subtract": 0x6D, "kp_enter": 0x0D,
}
for _letter in "abcdefghijklmnopqrstuvwxyz":
    WIN_VK_BY_NAME[_letter] = ord(_letter.upper())
for _digit in "0123456789":
    WIN_VK_BY_NAME[_digit] = ord(_digit)


def _bind_layout_independent_keys(
    root: Any,
    bindings: list[dict[str, Any]],
    is_text_focus_fn: Callable[[Any], bool] | None = None,
) -> None:
    """Bind keyboard shortcuts that work regardless of keyboard layout.

    Each binding is a dict with these keys:
        "key": "h" | "z" | "space" | "return" | ... (looked up in
                WIN_VK_BY_NAME for the VK code).
        "alt_keysyms": optional list of keysyms (lower-case) that should
                also trigger this binding when matched.
        "shift": True | False | None (None = "don't care")
        "ctrl":  True | False | None
        "alt":   True | False | None
        "callback": callable(event) -> None
        "allow_in_text": True to fire even when an Entry/Text widget
                has focus. Default False.

    Earlier entries win when multiple match.
    """
    parsed: list[dict[str, Any]] = []
    for spec in bindings:
        key_name = str(spec.get("key", "")).lower()
        vk = WIN_VK_BY_NAME.get(key_name)
        keysyms: set[str] = set()
        if key_name:
            keysyms.add(key_name)
        # Add convenient aliases.
        if key_name in ("plus", "equal"):
            keysyms.update({"plus", "equal", "kp_add"})
        elif key_name == "minus":
            keysyms.update({"minus", "kp_subtract"})
        elif key_name in ("return", "enter"):
            keysyms.update({"return", "kp_enter"})
        for alt in spec.get("alt_keysyms", []) or []:
            keysyms.add(str(alt).lower())
        parsed.append({
            "vk": vk,
            "keysyms": keysyms,
            "shift": spec.get("shift"),
            "ctrl": spec.get("ctrl"),
            "alt": spec.get("alt"),
            "callback": spec.get("callback"),
            "allow_in_text": spec.get("allow_in_text", False),
        })

    def _matches(event: Any, entry: dict[str, Any]) -> bool:
        state = getattr(event, "state", 0) or 0
        shift_held = bool(state & 0x0001)
        ctrl_held = bool(state & 0x0004)
        # Windows: Alt = 0x20000. X11 Mod1: 0x0008.
        alt_held = bool(state & 0x20000) or bool(state & 0x0008)
        for name, val in (("shift", shift_held), ("ctrl", ctrl_held), ("alt", alt_held)):
            required = entry.get(name)
            if required is None:
                continue
            if bool(required) != val:
                return False
        vk = entry.get("vk")
        if vk is not None and event.keycode == vk:
            return True
        return (event.keysym or "").lower() in entry["keysyms"]

    def _dispatch(event: Any) -> Any:
        for entry in parsed:
            if not entry["allow_in_text"] and is_text_focus_fn and is_text_focus_fn(event):
                continue
            if _matches(event, entry):
                cb = entry["callback"]
                if cb is not None:
                    cb(event)
                return "break"
        return None

    # Use add="+" so we don't clobber any existing bindings on root.
    root.bind("<KeyPress>", _dispatch, add="+")


class _PreviewScheduler:
    """Debounced, asynchronous, cached frame-extraction pump for GUIs.

    The extract callable runs on a worker thread so the Tk main loop stays
    responsive while the user scrubs. Worker results are marshalled back to
    the main thread through a thread-safe queue that is drained from a
    periodic Tk after() poll - calling root.after() directly from a worker
    thread is not safe across all Tk builds.

    Usage:
        scheduler = _PreviewScheduler(
            root,
            extract_fn=lambda t, w, h: extract_crop_preview_frame(answers, temp_dir, w, h, t),
            on_ready=lambda path: my_redraw(path),
            debounce_ms=70,
        )
        cached = scheduler.request(timestamp_s, width, height)
        if cached is not None:
            draw_image(cached)
        # else: extraction was scheduled; on_ready will fire from the main thread.

    Call scheduler.cancel() before destroying the root window.
    """

    def __init__(
        self,
        root: Any,
        extract_fn: Callable[[float, int, int], Path],
        on_ready: Callable[[Path], None],
        debounce_ms: int = 70,
        poll_ms: int = 25,
    ) -> None:
        import queue

        self._root = root
        self._extract = extract_fn
        self._on_ready = on_ready
        self._debounce_ms = max(0, int(debounce_ms))
        self._poll_ms = max(5, int(poll_ms))
        self._cache: dict[tuple[int, int, int], Path] = {}
        self._after_id: str | None = None
        self._poll_id: str | None = None
        self._pending: tuple[tuple[int, int, int], float, int, int] | None = None
        self._queued_next: tuple[tuple[int, int, int], float, int, int] | None = None
        self._worker_busy = False
        self._destroyed = False
        self._results: queue.Queue = queue.Queue()
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if self._destroyed:
            return
        try:
            self._poll_id = self._root.after(self._poll_ms, self._drain_results)
        except Exception:
            self._poll_id = None

    def _drain_results(self) -> None:
        import queue

        self._poll_id = None
        if self._destroyed:
            return
        try:
            while True:
                key, path = self._results.get_nowait()
                self._worker_busy = False
                if path is not None:
                    self._cache[key] = path
                    try:
                        self._on_ready(path)
                    except Exception:
                        pass
                nxt = self._queued_next
                self._queued_next = None
                if nxt is not None:
                    self._launch(nxt)
        except queue.Empty:
            pass
        self._schedule_poll()

    def cancel(self) -> None:
        self._destroyed = True
        for attr in ("_after_id", "_poll_id"):
            after_id = getattr(self, attr, None)
            if after_id is not None:
                try:
                    self._root.after_cancel(after_id)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._pending = None
        self._queued_next = None

    def clear_cache(self) -> None:
        self._cache.clear()

    def request(self, timestamp: float, width: int, height: int) -> Path | None:
        """Ask for a frame. Returns the cached Path immediately when known,
        otherwise schedules an asynchronous extraction and returns None."""
        if self._destroyed:
            return None
        key = (int(round(float(timestamp) * 1000)), int(width), int(height))
        cached = self._cache.get(key)
        if cached is not None and cached.exists():
            return cached
        self._pending = (key, float(timestamp), int(width), int(height))
        if self._after_id is not None:
            try:
                self._root.after_cancel(self._after_id)
            except Exception:
                pass
        self._after_id = self._root.after(self._debounce_ms, self._fire)
        return None

    def _fire(self) -> None:
        self._after_id = None
        if self._destroyed or self._pending is None:
            return
        if self._worker_busy:
            # Worker is mid-flight; remember this as the next-up request.
            self._queued_next = self._pending
            self._pending = None
            return
        request = self._pending
        self._pending = None
        self._launch(request)

    def _launch(self, request: tuple[tuple[int, int, int], float, int, int]) -> None:
        self._worker_busy = True
        key, timestamp, width, height = request

        def work() -> None:
            try:
                path = self._extract(timestamp, width, height)
            except Exception:
                path = None
            try:
                self._results.put((key, path))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()


# ============================================================
# PySide6 GUI bridge.
#
# The new dedicated GUI lives in ffmwiz_gui.py and is launched as a
# subprocess so the Qt and Tk worlds never share an event loop. Input
# and output use small JSON files via two --request / --reply CLI args.
#
# If PySide6 is not installed, _launch_qt_gui returns None and the
# caller falls back to the legacy Tk preview window.
# ============================================================

FFMWIZ_GUI_FILE_NAME = "ffmwiz_gui.py"
REQUIREMENTS_FILE_NAME = "requirements.txt"
PYSIDE6_DISPLAY_NAME = "PySide6"
PYSIDE6_PIP_SPEC = "PySide6==6.11.1"

# Module-level cache for the PySide6 availability probe so we only shell
# out once per process. Set to True/False the first time the answer is
# known. Reset to None after a successful install so we re-probe.
_PYSIDE6_AVAILABLE_CACHE: bool | None = None


def _ffmwiz_gui_path() -> Path:
    return script_dir() / FFMWIZ_GUI_FILE_NAME


def _requirements_path() -> Path:
    return script_dir() / REQUIREMENTS_FILE_NAME


def _probe_pyside6() -> bool:
    """Fast PySide6 availability check without importing Qt in the CLI process."""
    try:
        import importlib.util

        return (
            importlib.util.find_spec("PySide6") is not None
            and importlib.util.find_spec("PySide6.QtWidgets") is not None
        )
    except Exception:
        return False


def _pyside6_available() -> bool:
    """Cached PySide6 detection."""
    global _PYSIDE6_AVAILABLE_CACHE
    if _PYSIDE6_AVAILABLE_CACHE is not None:
        return _PYSIDE6_AVAILABLE_CACHE
    if not _ffmwiz_gui_path().exists():
        _PYSIDE6_AVAILABLE_CACHE = False
        return False
    _PYSIDE6_AVAILABLE_CACHE = _probe_pyside6()
    return _PYSIDE6_AVAILABLE_CACHE


def ensure_pyside6_installed(interactive: bool = True) -> bool:
    """Make sure PySide6 is importable. On first run, offers to install it
    automatically with pip. Returns True if PySide6 is available afterwards.

    Environment overrides:
        FFMWIZ_NO_AUTO_INSTALL=1   Skip the install prompt entirely; just
                                    fall back to the legacy Tk GUI.
        FFMWIZ_AUTO_INSTALL=1      Skip the confirmation and install
                                    without asking (good for unattended
                                    setups, CI, scripts).
    """
    global _PYSIDE6_AVAILABLE_CACHE
    if _pyside6_available():
        return True

    if os.environ.get("FFMWIZ_NO_AUTO_INSTALL"):
        return False

    # Make sure the GUI file is present; installing the runtime is pointless
    # if the actual GUI module is missing.
    if not _ffmwiz_gui_path().exists():
        return False

    auto = bool(
        os.environ.get("FFMWIZ_AUTO_INSTALL")
        or os.environ.get("FFMWIZ_AUTO_INSTALL_PYSIDE")
    )

    print()
    note(
        f"{PYSIDE6_DISPLAY_NAME} is not installed. The dedicated Cut Editor and "
        f"Crop Editor GUIs need it for smooth playback and a professional UI."
    )

    proceed = auto
    if not auto and interactive:
        try:
            choice = input(
                f"Install {PYSIDE6_DISPLAY_NAME} now via pip? [Y/n] "
                "(Enter=Yes; set FFMWIZ_NO_AUTO_INSTALL=1 to skip in the future): "
            ).strip().lower()
            proceed = choice in {"", "y", "yes"}
        except (EOFError, KeyboardInterrupt):
            proceed = False

    if not proceed:
        note(
            f"Skipping. FFmWiz will use the legacy Tk GUI for now. "
            f"Install later with:  py -3 -m pip install -r {REQUIREMENTS_FILE_NAME}"
        )
        return False

    # Try the system-wide install first. If pip cannot write to the
    # interpreter's site-packages (very common on Windows for
    # installations under "Program Files"), automatically retry with
    # --user so the install succeeds for the current user.
    requirements_path = _requirements_path()
    install_target = ["-r", str(requirements_path)] if requirements_path.exists() else [PYSIDE6_PIP_SPEC]
    base_cmd = [sys.executable, "-m", "pip", "install", "--upgrade"]
    attempts: list[list[str]] = [
        base_cmd + install_target,
        base_cmd + ["--user"] + install_target,
    ]
    install_ok = False
    for attempt_idx, cmd in enumerate(attempts):
        print()
        note("Running: " + " ".join(cmd))
        print()
        try:
            # Inherit stdout/stderr so the user sees pip's progress live.
            # The install can be ~150 MB and the user needs visibility.
            result = subprocess.run(cmd, check=False)
        except FileNotFoundError as exc:
            error(f"Could not run pip ({exc}). Falling back to the legacy Tk GUI.")
            return False
        except Exception as exc:
            error(f"Pip install failed: {exc}.")
            continue
        if result.returncode == 0:
            install_ok = True
            break
        if attempt_idx + 1 < len(attempts):
            note(
                f"pip install exited with code {result.returncode}. "
                "Retrying with --user (per-user install) ..."
            )

    if not install_ok:
        error(
            f"{PYSIDE6_DISPLAY_NAME} install failed. Falling back to the legacy Tk "
            f"GUI. You can retry manually with:  py -3 -m pip install --user -r {REQUIREMENTS_FILE_NAME}"
        )
        return False

    # Re-probe so the cache picks up the newly installed package.
    _PYSIDE6_AVAILABLE_CACHE = None
    if _pyside6_available():
        note(f"{PYSIDE6_DISPLAY_NAME} installed. The new GUI is now active.")
        return True
    error(
        f"{PYSIDE6_DISPLAY_NAME} install completed but the package still cannot "
        "be imported. Falling back to the legacy Tk GUI."
    )
    return False


def _launch_qt_gui(request: dict[str, Any]) -> dict[str, Any] | None:
    """Launch ffmwiz_gui.py as a subprocess, hand it the request via a
    temp JSON file, and return the parsed reply dict.

    Returns None only when the dedicated GUI is unavailable before launch
    (missing PySide6, missing ffmwiz_gui.py, etc.). Once the Qt GUI starts,
    internal GUI errors are returned as {"status": "error", ...} so callers
    do not hide real bugs behind the legacy Tk fallback.
    """
    gui_path = _ffmwiz_gui_path()
    if not gui_path.exists():
        return None
    if not _pyside6_available():
        return None

    request_payload = dict(request)
    # Serialize Path objects to plain strings for JSON.
    for key, value in list(request_payload.items()):
        if isinstance(value, Path):
            request_payload[key] = str(value)
    request_payload["parent_pid"] = os.getpid()

    with tempfile.TemporaryDirectory(prefix="ffmwiz_ipc_") as tmp:
        tmp_path = Path(tmp)
        req_path = tmp_path / "request.json"
        rep_path = tmp_path / "reply.json"
        req_path.write_text(json.dumps(request_payload, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, str(gui_path),
               "--request", str(req_path),
               "--reply", str(rep_path)]
        try:
            result = subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            tb = traceback.format_exc()
            log_exception(f"Qt GUI subprocess failed: {exc}")
            return {"status": "error", "message": f"Qt GUI subprocess failed: {exc}", "traceback": tb}
        if result.stdout:
            log_debug("Qt GUI stdout: " + result.stdout.rstrip())
        if result.stderr:
            log_error("Qt GUI stderr: " + result.stderr.rstrip())
        if result.returncode != 0:
            try:
                payload = json.loads(rep_path.read_text(encoding="utf-8"))
            except Exception:
                payload = None
            if isinstance(payload, dict) and payload.get("status") == "error":
                message = payload.get("message") or "Qt GUI failed."
                error(f"Qt GUI reported: {message}")
                if payload.get("traceback"):
                    log_error(payload["traceback"])
                if os.environ.get("FFMWIZ_DEBUG") and payload.get("traceback"):
                    print(payload["traceback"])
                return payload
            message = f"Qt GUI exited with code {result.returncode}."
            log_error(message)
            return {"status": "error", "message": message}
        if not rep_path.exists():
            message = "Qt GUI exited without writing a reply file."
            log_error(message)
            return {"status": "error", "message": message}
        try:
            payload = json.loads(rep_path.read_text(encoding="utf-8"))
            log_info(f"Qt GUI returned status={payload.get('status')}")
            return payload
        except Exception as exc:
            tb = traceback.format_exc()
            log_exception(f"Could not parse Qt GUI reply: {exc}")
            if os.environ.get("FFMWIZ_DEBUG"):
                print(tb)
            return {"status": "error", "message": f"Could not parse Qt GUI reply: {exc}", "traceback": tb}


# =====================================================================
# Logging system.
#
# Logging is enabled by default and writes to a dated UTF-8 file in the
# Logs/ folder next to this script. The console stays clean and readable;
# everything technical (full FFmpeg stderr, generated commands, tracebacks,
# parameter dumps) goes into the log file.
# =====================================================================

LOGS_DIR_NAME = "Logs"
_LOG_PATH: Path | None = None
_LOGGER: logging.Logger | None = None


def _logs_dir() -> Path:
    return script_dir() / LOGS_DIR_NAME


def _config_setting_for_logging(key: str, fallback: Any) -> Any:
    try:
        path = script_dir() / CONFIG_FILE_NAME
        if not path.exists():
            return fallback
        data = json.loads(path.read_text(encoding="utf-8"))
        settings = data.get("settings", {}) if isinstance(data, dict) else {}
        if isinstance(settings, dict) and key in settings:
            return settings.get(key)
    except Exception:
        return fallback
    return fallback


def _logging_enabled_from_config() -> bool:
    value = _config_setting_for_logging("logging_enabled", True)
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    return parse_bool_config(str(value).strip(), True)


def _log_retention_days_from_config() -> int:
    value = _config_setting_for_logging("log_retention_days", 0)
    try:
        return max(0, int(str(value).strip()))
    except Exception:
        return 0


def _prune_old_logs(logs_dir: Path, retention_days: int) -> None:
    if retention_days <= 0:
        return
    cutoff = time.time() - retention_days * 86400
    for path in logs_dir.glob("ffmwiz_*.log"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except Exception:
            pass


def setup_logging() -> Path | None:
    """Initialize the file logger (UTF-8, dated filename) and return the
    log path. Subsequent calls are no-ops and return the existing path."""
    global _LOG_PATH, _LOGGER
    if _LOGGER is not None:
        return _LOG_PATH
    if not _logging_enabled_from_config():
        _LOGGER = None
        _LOG_PATH = None
        return None
    try:
        logs_dir = _logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)
        _prune_old_logs(logs_dir, _log_retention_days_from_config())
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        _LOG_PATH = logs_dir / f"ffmwiz_{stamp}.log"
        logger = logging.getLogger("ffmwiz")
        logger.setLevel(logging.DEBUG)
        # Wipe any handlers added by previous runs in the same process.
        for h in list(logger.handlers):
            logger.removeHandler(h)
        handler = logging.FileHandler(_LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.propagate = False
        _LOGGER = logger
        return _LOG_PATH
    except Exception:
        # Logging must never break the wizard.
        _LOGGER = None
        _LOG_PATH = None
        return None


def log_path() -> Path | None:
    return _LOG_PATH


def log_info(msg: str) -> None:
    if _LOGGER is not None:
        try:
            _LOGGER.info(msg)
        except Exception:
            pass


def log_warn(msg: str) -> None:
    if _LOGGER is not None:
        try:
            _LOGGER.warning(msg)
        except Exception:
            pass


def log_error(msg: str) -> None:
    if _LOGGER is not None:
        try:
            _LOGGER.error(msg)
        except Exception:
            pass


def log_debug(msg: str) -> None:
    if _LOGGER is not None:
        try:
            _LOGGER.debug(msg)
        except Exception:
            pass


def log_exception(msg: str) -> None:
    if _LOGGER is not None:
        try:
            _LOGGER.exception(msg)
        except Exception:
            pass


def log_environment(extra: dict[str, Any] | None = None) -> None:
    """Log app/system/python/ffmpeg environment so every run is traceable."""
    log_info("=" * 72)
    log_info(f"FFmWiz session start at {datetime.datetime.now().isoformat()}")
    try:
        log_info(f"OS: {platform.system()} {platform.release()} ({platform.version()})")
    except Exception:
        pass
    log_info(f"Python: {sys.version.replace(chr(10), ' ')}")
    log_info(f"Executable: {sys.executable}")
    log_info(f"Script: {Path(__file__).resolve()}")
    log_info(f"CWD: {Path.cwd()}")
    if extra:
        for k, v in extra.items():
            log_info(f"{k}: {v}")


def log_command(label: str, cmd: list[str]) -> None:
    quoted = " ".join(c if " " not in c else f'"{c}"' for c in cmd)
    log_info(f"{label} command: {quoted}")


# =====================================================================
# FFmpeg progress display.
#
# Injects -nostats -progress pipe:1 into an FFmpeg command so the
# binary writes machine-readable key=value lines to stdout while the
# console gets a single, in-place updating status line that shows
# FFmpeg-style frame/fps/q/size/time/bitrate/speed/elapsed/ETA fields.
# Raw FFmpeg stderr is captured into the log file.
# =====================================================================


def _inject_progress_args(cmd: list[str]) -> list[str]:
    """Insert FFmpeg progress flags right after the binary path so the
    progress + log levels apply to all outputs of the command."""
    if len(cmd) < 1:
        return cmd
    new = [cmd[0]]
    # -nostats suppresses noisy stderr summary lines, -progress pipe:1
    # streams structured key=value progress to stdout, -loglevel warning
    # keeps real warnings/errors flowing into our captured stderr.
    new.extend(["-nostats", "-progress", "pipe:1", "-loglevel", "warning"])
    new.extend(cmd[1:])
    return new


def _human_size(b: int) -> str:
    if b is None or b < 0:
        return "?"
    size = float(b)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def _visible_len(text: str) -> int:
    return len(_strip_ansi(text))


def _truncate_ansi_visible(text: str, max_visible: int) -> str:
    if _visible_len(text) <= max_visible:
        return text
    limit = max(1, max_visible - 3)
    out: list[str] = []
    visible = 0
    idx = 0
    while idx < len(text) and visible < limit:
        match = ANSI_ESCAPE_RE.match(text, idx)
        if match:
            out.append(match.group(0))
            idx = match.end()
            continue
        out.append(text[idx])
        visible += 1
        idx += 1
    truncated = "".join(out) + "..."
    if "\x1b[" in truncated and not truncated.endswith(Color.RESET):
        truncated += Color.RESET
    return truncated


def _progress_colorize(text: str, color: str, enabled: bool) -> str:
    return paint(text, color) if enabled else text


def _progress_terminal_width() -> int:
    try:
        return max(60, shutil.get_terminal_size((100, 20)).columns)
    except Exception:
        return 100


def _join_progress_segments(
    segments: list[tuple[str, str]],
    colorize: bool,
    separator: str = "  •  ",
) -> str:
    sep = _progress_colorize(separator, PROGRESS_COLORS["separator"], colorize)
    return sep.join(text if not color else _progress_colorize(text, color, colorize) for text, color in segments)


def _render_progress_line(state: dict[str, str], total_duration: float | None,
                          started_at: float, max_width: int | None = None) -> str:
    """Format a single FFmpeg progress status line."""
    try:
        current_s = int(state.get("out_time_ms", "0")) / 1_000_000.0
    except (ValueError, TypeError):
        current_s = 0.0

    def q_value() -> str:
        for key in ("stream_0_0_q", "q"):
            value = state.get(key)
            if value:
                return value
        for key, value in state.items():
            if key.endswith("_q") and value:
                return value
        return "N/A"

    def output_size() -> str:
        value = state.get("total_size", "")
        if value.isdigit():
            return (
                _human_size(max(0, int(value)))
                .replace("KiB", "KB")
                .replace("MiB", "MB")
                .replace("GiB", "GB")
                .replace("TiB", "TB")
            )
        return "N/A"

    def bitrate_value() -> str:
        value = state.get("bitrate", "N/A") or "N/A"
        return value

    def fps_value() -> str:
        value = state.get("fps", "N/A") or "N/A"
        return "N/A" if value in {"0", "0.0", "0.00"} else value

    elapsed = max(0.0, time.perf_counter() - started_at)
    if total_duration and total_duration > 0 and current_s >= total_duration * 0.995:
        eta_s = 0.0
    elif total_duration and current_s > 0.5 and elapsed > 0.5:
        speed_ratio = current_s / elapsed
        eta_s = max(0.0, (total_duration - current_s) / speed_ratio) if speed_ratio > 0.01 else None
    else:
        eta_s = None

    colorize = USE_COLOR
    if total_duration and total_duration > 0:
        pct = min(100.0, 100.0 * current_s / total_duration)
        pct_text = f"{pct:.1f}%"
    else:
        pct_text = "progress"

    current_text = format_progress_clock(current_s)
    total_text = format_progress_clock(total_duration) if total_duration and total_duration > 0 else "unknown"
    elapsed_text = format_progress_elapsed_dot(elapsed)
    eta_text = format_progress_duration(eta_s) if eta_s is not None else "calculating"
    time_total_text = (
        f"{_progress_colorize(f'time {current_text}', PROGRESS_COLORS['time'], colorize)} "
        f"{_progress_colorize(f'/ {total_text}', PROGRESS_COLORS['total'], colorize)}"
    )
    eta_segment = (
        f"{_progress_colorize('ETA', PROGRESS_COLORS['eta_label'], colorize)} "
        f"{_progress_colorize(eta_text, PROGRESS_COLORS['eta_value'], colorize)}"
    )

    verbose_segments = [
        (pct_text, PROGRESS_COLORS["percent"]),
        (time_total_text, ""),
        (f"fps {fps_value()}", PROGRESS_COLORS["fps"]),
        (f"q {q_value()}", PROGRESS_COLORS["q"]),
        (f"speed {state.get('speed', 'N/A') or 'N/A'}", PROGRESS_COLORS["speed"]),
        (f"size {output_size()}", PROGRESS_COLORS["size"]),
        (f"bitrate {bitrate_value()}", PROGRESS_COLORS["bitrate"]),
        (f"elapsed {elapsed_text}", PROGRESS_COLORS["elapsed"]),
        (eta_segment, ""),
    ]
    return _join_progress_segments(verbose_segments, colorize)


def preview_console_colors() -> None:
    """Print a small ANSI color preview for users editing FFmWiz colors."""
    print(paint("FFmWiz color preview", Color.BOLD + Color.LIGHT_BLUE))
    print()
    print("Base colors:")
    for name in (
        "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE",
        "GRAY", "ORANGE", "LIGHT_BLUE", "LIGHT_YELLOW", "HINT_YELLOW",
        "AQUA", "PINK", "LIME", "KEEP_VALUE", "RES_NUMBERS",
        "RES_TARGET", "RES_EXACT", "AUDIO_ALL", "AUDIO_DROP_DUP",
        "AUDIO_DROP_EMPTY", "AUDIO_DROP_BOTH", "AUDIO_TRACK_NOTE",
        "FINAL_COMMAND_LABEL", "FINAL_COMMAND_TEXT", "SUGGESTION",
        "BACK_PROMPT", "EXIT_PROMPT", "NEAR_EMPTY", "ZERO_INLINE", "PROGRESS_PERCENT",
        "PROGRESS_TIME", "PROGRESS_FPS", "PROGRESS_Q", "PROGRESS_SPEED",
        "PROGRESS_SIZE", "PROGRESS_BITRATE", "PROGRESS_ELAPSED",
        "PROGRESS_ETA_LABEL", "PROGRESS_ETA_VALUE",
    ):
        code = getattr(Color, name)
        print(f"  {name:13s} {paint('Sample text', code)}  {repr(code)}")
    print()
    print("Prompt keep-current sample:")
    print("  " + keep_value_text("n=current resolution"))
    print("  " + keep_value_text("n=current FPS around 30"))
    print("  " + keep_value_text("n=keep current value around 359k"))
    print()
    sample_state = {
        "out_time_ms": str(int((19 * 60 + 1) * 1_000_000)),
        "fps": "63.70",
        "stream_0_0_q": "9.0",
        "speed": "15.9x",
        "total_size": str(int(18.8 * 1024 * 1024)),
        "bitrate": "137.8kbits/s",
        "progress": "continue",
    }
    started_at = time.perf_counter() - 62.0
    print("Progress full:")
    print("  " + _render_progress_line(sample_state, 79 * 60 + 5, started_at))
    print("Progress 100-column preview:")
    print("  " + _render_progress_line(sample_state, 79 * 60 + 5, started_at, max_width=100))


_VT_MODE_ATTEMPTED = False
_PROGRESS_LAST_LEN = 0
_PROGRESS_LAST_ROWS = 0
_WINDOWS_CONSOLE_CHECKED = False
_WINDOWS_CONSOLE_OK = False


def _enable_windows_vt_mode() -> None:
    global _VT_MODE_ATTEMPTED
    if _VT_MODE_ATTEMPTED or os.name != "nt":
        return
    _VT_MODE_ATTEMPTED = True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if handle and kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception as exc:
        log_debug(f"Could not enable Windows VT console mode: {exc}")


def _stdout_supports_in_place_progress() -> bool:
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False
    if os.name != "nt":
        return True
    global _WINDOWS_CONSOLE_CHECKED, _WINDOWS_CONSOLE_OK
    if _WINDOWS_CONSOLE_CHECKED:
        return _WINDOWS_CONSOLE_OK
    _WINDOWS_CONSOLE_CHECKED = True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        _WINDOWS_CONSOLE_OK = bool(handle and kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception as exc:
        log_debug(f"Windows console progress support check failed: {exc}")
        _WINDOWS_CONSOLE_OK = False
    return _WINDOWS_CONSOLE_OK


def _write_progress_line(rendered: str) -> None:
    global _PROGRESS_LAST_LEN, _PROGRESS_LAST_ROWS
    if not _stdout_supports_in_place_progress():
        return
    _enable_windows_vt_mode()
    width = _progress_terminal_width()
    visible = _visible_len(rendered)
    rows = max(1, (visible + max(1, width - 1) - 1) // max(1, width - 1))
    sys.stdout.write("\r")
    for _ in range(max(0, _PROGRESS_LAST_ROWS - 1)):
        sys.stdout.write("\033[2K\033[1A\r")
    sys.stdout.write("\033[2K" + rendered)
    sys.stdout.flush()
    _PROGRESS_LAST_LEN = visible
    _PROGRESS_LAST_ROWS = rows


def _finish_progress_line(rendered: str | None) -> None:
    global _PROGRESS_LAST_LEN, _PROGRESS_LAST_ROWS
    if rendered:
        if not _stdout_supports_in_place_progress():
            sys.stdout.write(rendered + "\n")
            sys.stdout.flush()
            _PROGRESS_LAST_LEN = 0
            _PROGRESS_LAST_ROWS = 0
            return
        _enable_windows_vt_mode()
        width = _progress_terminal_width()
        visible = _visible_len(rendered)
        sys.stdout.write("\r")
        for _ in range(max(0, _PROGRESS_LAST_ROWS - 1)):
            sys.stdout.write("\033[2K\033[1A\r")
        sys.stdout.write("\033[2K" + rendered + "\n")
    else:
        sys.stdout.write("")
    sys.stdout.flush()
    _PROGRESS_LAST_LEN = 0
    _PROGRESS_LAST_ROWS = 0


def run_ffmpeg_with_progress(
    cmd: list[str],
    total_duration: float | None = None,
    label: str = "FFmpeg",
) -> tuple[int, float]:
    """Run an FFmpeg command and render an in-place progress line.

    - Injects -nostats -progress pipe:1 -loglevel warning.
    - stdout is parsed as key=value progress.
    - stderr is captured into the log file (not the console) so warnings
      and errors are preserved without flooding the terminal.
    - When FFmpeg signals 'progress=end', the final line is committed
      with a newline so subsequent output starts cleanly.

    Returns (returncode, elapsed_seconds).
    """
    progress_cmd = _inject_progress_args(cmd)
    log_command(label, cmd)

    started_at = time.perf_counter()
    state: dict[str, str] = {}
    last_render = ""
    stderr_lines: list[str] = []
    final_emitted = False
    progress_events = 0

    try:
        process = subprocess.Popen(
            progress_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        log_exception(f"Failed to start {label}")
        error(f"Failed to start {label}: {exc}")
        return 1, 0.0

    # Capture stderr into the log in a worker so the main thread can
    # render progress without blocking on stderr drain.
    def _capture_stderr() -> None:
        try:
            for line in process.stderr:  # type: ignore[union-attr]
                stripped = line.rstrip()
                if not stripped:
                    continue
                stderr_lines.append(stripped)
                log_debug(f"{label} stderr: {stripped}")
        except Exception:
            pass

    stderr_thread = threading.Thread(target=_capture_stderr, daemon=True)
    stderr_thread.start()

    try:
        for line in process.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if line:
                log_debug(f"{label} stdout: {line}")
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            state[key.strip()] = value.strip()
            if key.strip() != "progress":
                continue
            progress_events += 1
            rendered = _render_progress_line(state, total_duration, started_at)
            _write_progress_line(rendered)
            last_render = rendered
            if os.environ.get("FFMWIZ_DEBUG_PROGRESS"):
                log_debug(f"{label} progress event #{progress_events}: {_strip_ansi(rendered)}")
            if value.strip() == "end":
                _finish_progress_line(rendered)
                final_emitted = True
    except Exception:
        log_exception(f"{label} progress reader crashed")

    process.wait()
    stderr_thread.join()
    elapsed = time.perf_counter() - started_at

    if not final_emitted:
        _finish_progress_line(last_render or None)
    log_debug(f"{label} progress parser events: {progress_events}")

    if process.returncode != 0:
        log_error(f"{label} exited with code {process.returncode}")
        tail = "\n".join(stderr_lines[-12:])
        if tail:
            log_error(f"{label} stderr tail:\n{tail}")
        if _LOG_PATH is not None:
            error(f"{label} failed. Full FFmpeg output is in: {_LOG_PATH}")
        else:
            error(f"{label} failed.")
    else:
        log_info(f"{label} completed successfully in {format_elapsed(elapsed)}")

    return process.returncode, elapsed


def startup_line(label: str, message: str, label_color: str, message_color: str = Color.WHITE) -> None:
    print(f"{paint(label + ':', label_color)} {paint(message, message_color)}")


def format_crop_margins(answers: dict[str, Any]) -> str:
    return (
        f"top={answers.get('crop_top', 0)} px, "
        f"left={answers.get('crop_left', 0)} px, "
        f"right={answers.get('crop_right', 0)} px, "
        f"bottom={answers.get('crop_bottom', 0)} px"
    )


def format_resolution_summary(value: Any) -> str:
    if value is None or value == "n":
        return "source"
    if isinstance(value, dict):
        mode = value.get("mode")
        if mode == "preset":
            return f"{value.get('label')} closest-edge preset"
        if mode == "box":
            return f"{value.get('width')}x{value.get('height')} preserve-aspect box"
        if mode == "height":
            return f"{value.get('height')}p target height"
        if mode == "width":
            return f"{value.get('width')}w target width"
        if mode == "exact_stretch":
            return f"{value.get('width')}x{value.get('height')} exact stretch"
    if isinstance(value, tuple) and len(value) == 2:
        return f"{value[0]}x{value[1]}"
    return str(value)


def even_dimension(value: float | int) -> int:
    number = max(2, int(round(float(value))))
    return number if number % 2 == 0 else number + 1


def cropped_source_size(answers: dict[str, Any]) -> tuple[int, int]:
    source_w, source_h = first_video_size(answers)
    if not answers.get("crop_enabled"):
        return source_w, source_h
    left = int(answers.get("crop_left", 0) or 0)
    right = int(answers.get("crop_right", 0) or 0)
    top = int(answers.get("crop_top", 0) or 0)
    bottom = int(answers.get("crop_bottom", 0) or 0)
    message = crop_margins_validation_message(answers, top, left, right, bottom)
    if message:
        raise ValueError(message)
    return source_w - left - right, source_h - top - bottom


def crop_margins_validation_message(
    answers: dict[str, Any],
    top: int,
    left: int,
    right: int,
    bottom: int,
) -> str | None:
    source_w, source_h = first_video_size(answers)
    if min(top, left, right, bottom) < 0:
        return "Invalid crop margins: crop values must be zero or positive."
    if left + right >= source_w:
        return (
            f"Invalid crop margins: left + right ({left + right} px) must be "
            f"smaller than source width ({source_w} px)."
        )
    if top + bottom >= source_h:
        return (
            f"Invalid crop margins: top + bottom ({top + bottom} px) must be "
            f"smaller than source height ({source_h} px)."
        )
    return None


def set_crop_margins_if_valid(
    answers: dict[str, Any],
    top: int,
    left: int,
    right: int,
    bottom: int,
) -> bool:
    message = crop_margins_validation_message(answers, top, left, right, bottom)
    if message:
        error(message)
        log_warn(message)
        return False
    answers["crop_enabled"] = any((top, left, right, bottom))
    answers["crop_top"] = top
    answers["crop_left"] = left
    answers["crop_right"] = right
    answers["crop_bottom"] = bottom
    return True


def set_single_crop_margin_if_valid(answers: dict[str, Any], key: str, value: int) -> bool:
    old_marker = object()
    old_value = answers.get(key, old_marker)
    answers[key] = value
    top = int(answers.get("crop_top", 0) or 0)
    left = int(answers.get("crop_left", 0) or 0)
    right = int(answers.get("crop_right", 0) or 0)
    bottom = int(answers.get("crop_bottom", 0) or 0)
    message = crop_margins_validation_message(answers, top, left, right, bottom)
    if not message:
        return True
    if old_value is old_marker:
        answers.pop(key, None)
    else:
        answers[key] = old_value
    error(message)
    log_warn(message)
    return False


def closest_edge_scale_dimensions(
    crop_w: int,
    crop_h: int,
    target_w: int,
    target_h: int,
) -> tuple[int, int, str]:
    source_landscape = crop_w >= crop_h
    target_landscape = target_w >= target_h
    if source_landscape != target_landscape:
        target_w, target_h = target_h, target_w
    width_distance = abs(crop_w - target_w)
    height_distance = abs(crop_h - target_h)
    if width_distance <= height_distance:
        final_width = even_dimension(target_w)
        final_height = even_dimension(final_width * crop_h / max(1, crop_w))
        return final_width, final_height, "width"
    final_height = even_dimension(target_h)
    final_width = even_dimension(final_height * crop_w / max(1, crop_h))
    return final_width, final_height, "height"


def calculate_scale_dimensions(answers: dict[str, Any], resolution: Any) -> tuple[tuple[int, int] | None, str]:
    if resolution is None or resolution == "n":
        return None, ""

    crop_w, crop_h = cropped_source_size(answers)
    answers["crop_box_dimensions"] = (crop_w, crop_h)
    answers["cropped_aspect_ratio"] = crop_w / max(1, crop_h)
    warning_parts: list[str] = []
    axis = ""
    if isinstance(resolution, dict):
        mode = resolution.get("mode")
        if mode == "preset":
            width, height, axis = closest_edge_scale_dimensions(
                crop_w,
                crop_h,
                int(resolution.get("width", crop_w) or crop_w),
                int(resolution.get("height", crop_h) or crop_h),
            )
        elif mode == "box":
            width, height, axis = closest_edge_scale_dimensions(
                crop_w,
                crop_h,
                int(resolution.get("width", crop_w) or crop_w),
                int(resolution.get("height", crop_h) or crop_h),
            )
        elif mode == "height":
            height = even_dimension(resolution.get("height", crop_h))
            width = even_dimension(height * crop_w / max(1, crop_h))
            axis = "height"
        elif mode == "width":
            width = even_dimension(resolution.get("width", crop_w))
            height = even_dimension(width * crop_h / max(1, crop_w))
            axis = "width"
        elif mode == "exact_stretch":
            requested_w = int(resolution.get("width", crop_w) or crop_w)
            requested_h = int(resolution.get("height", crop_h) or crop_h)
            width = even_dimension(requested_w)
            height = even_dimension(requested_h)
            axis = "stretch"
            if (width, height) != (requested_w, requested_h):
                warning_parts.append(
                    f"exact stretch resolution adjusted to codec-safe even dimensions: {width}x{height}"
                )
            crop_ar = crop_w / max(1, crop_h)
            out_ar = width / max(1, height)
            if abs(crop_ar - out_ar) / max(crop_ar, 1e-9) > 0.01:
                warning_parts.append(
                    "exact stretch output differs from the cropped aspect ratio and will stretch the image"
                )
        else:
            raise ValueError(f"Unknown resolution mode: {mode!r}")
    elif isinstance(resolution, tuple) and len(resolution) == 2:
        # Backward compatibility for older in-memory callers.
        width = even_dimension(resolution[0])
        height = even_dimension(resolution[1])
        axis = "stretch"
    else:
        raise ValueError(f"Invalid resolution value: {resolution!r}")

    answers["resolution_scale_axis"] = axis
    answers["final_resolution"] = (width, height)
    return (width, height), "; ".join(warning_parts)


def resolve_scale_dimensions(answers: dict[str, Any], resolution: Any) -> tuple[int, int] | None:
    dimensions, warning_text = calculate_scale_dimensions(answers, resolution)
    if dimensions is None:
        answers.pop("final_resolution", None)
        answers.pop("crop_box_dimensions", None)
        answers.pop("cropped_aspect_ratio", None)
        answers.pop("resolution_scale_axis", None)
        return None

    crop_w, crop_h = cropped_source_size(answers)
    answers["crop_box_dimensions"] = (crop_w, crop_h)
    answers["cropped_aspect_ratio"] = crop_w / max(1, crop_h)
    width, height = dimensions
    answers["final_resolution"] = (width, height)
    if warning_text and answers.get("_resolution_warning_emitted") != warning_text:
        note("Resolution warning: " + warning_text + ".")
        answers["_resolution_warning_emitted"] = warning_text
    return width, height


def question_prompt(
    answers: dict[str, Any],
    title: str,
    details: str | None = None,
    default: str | None = None,
    back: str = "back=0, quit=exit",
) -> str:
    number = answers.get("_question_number", "?")
    prompt = paint(f"{number}. {title}", Color.BOLD)
    if details:
        prompt += f" ({paint(details, Color.HINT_YELLOW)})"
    if default is not None:
        prompt += f" {paint('[' + default + ']', Color.GREEN)}"
    if back:
        prompt += f" {back_text(back)}"
    return "\n" + prompt + ": "


def strip_quotes(value: str) -> str:
    value = value.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value


def normalize_terminal_path_text(value: str) -> str:
    value = strip_quotes(value)
    if value.startswith("&"):
        value = strip_quotes(value[1:].strip())
    if value.startswith("<") and value.endswith(">"):
        value = strip_quotes(value[1:-1].strip())

    parsed = urlparse(value)
    if parsed.scheme.lower() == "file":
        path_text = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:", path_text):
            path_text = path_text[1:]
        elif parsed.netloc:
            path_text = f"//{parsed.netloc}{path_text}"
        return path_text.replace("/", os.sep)

    return value


def terminal_path(value: str) -> Path:
    return Path(normalize_terminal_path_text(value)).expanduser()


def fail(message: str) -> None:
    error(f"\nERROR: {message}")
    sys.exit(1)


def _confirm_install(prompt: str, env_auto_name: str) -> bool:
    if os.environ.get(env_auto_name) or os.environ.get("FFMWIZ_AUTO_INSTALL"):
        return True
    if os.environ.get("FFMWIZ_NO_AUTO_INSTALL"):
        return False
    try:
        choice = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return choice in {"", "y", "yes"}


def _run_dependency_install(cmd: list[str], label: str) -> bool:
    print()
    note("Running: " + " ".join(cmd))
    log_info(f"Dependency install command for {label}: {cmd}")
    try:
        result = subprocess.run(cmd, check=False)
    except Exception as exc:
        log_exception(f"Could not start dependency installer for {label}")
        error(f"Could not start {label} installer. See log file: {_log_file_text()}")
        return False
    if result.returncode != 0:
        log_error(f"Dependency installer for {label} exited with code {result.returncode}")
        return False
    return True


def ensure_ffmpeg_tools_installed(interactive: bool = True) -> tuple[str | None, str | None]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    missing = []
    if not ffmpeg:
        missing.append("ffmpeg")
    if not ffprobe:
        missing.append("ffprobe")
    log_warn("Missing FFmpeg tools: " + ", ".join(missing))
    if not interactive:
        return ffmpeg, ffprobe

    print()
    note("Missing prerequisite: " + ", ".join(missing))
    note("FFmWiz needs the full FFmpeg package, including ffprobe.")

    installers: list[tuple[str, list[str]]] = []
    winget = shutil.which("winget")
    if winget:
        installers.append(("winget", [winget, "install", "--id", "Gyan.FFmpeg", "-e", "--source", "winget"]))
    choco = shutil.which("choco")
    if choco:
        installers.append(("Chocolatey", [choco, "install", "ffmpeg", "-y"]))

    if not installers:
        note("No supported package manager was found. Install FFmpeg manually and reopen FFmWiz.")
        return ffmpeg, ffprobe

    label, cmd = installers[0]
    if not _confirm_install(
        f"Install FFmpeg now using {label}? [Y/n] ",
        "FFMWIZ_AUTO_INSTALL_FFMPEG",
    ):
        return ffmpeg, ffprobe

    if _run_dependency_install(cmd, "FFmpeg"):
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg and ffprobe:
            note("FFmpeg tools are now available.")
            return ffmpeg, ffprobe
        note("Install finished, but ffmpeg/ffprobe are still not visible in this terminal. Reopen the terminal if PATH was updated.")

    if len(installers) > 1:
        fallback_label, fallback_cmd = installers[1]
        if _confirm_install(
            f"Try installing FFmpeg using {fallback_label} instead? [Y/n] ",
            "FFMWIZ_AUTO_INSTALL_FFMPEG",
        ) and _run_dependency_install(fallback_cmd, "FFmpeg"):
            ffmpeg = shutil.which("ffmpeg")
            ffprobe = shutil.which("ffprobe")
    return ffmpeg, ffprobe


def check_tools(interactive: bool = True) -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        ffmpeg, ffprobe = ensure_ffmpeg_tools_installed(interactive=interactive)
    if not ffmpeg:
        fail("ffmpeg is not installed or is not available in PATH. Install FFmpeg and add its bin folder to PATH.")
    if not ffprobe:
        fail("ffprobe was not found. Install the full FFmpeg package; ffprobe is normally included with it.")
    return ffmpeg, ffprobe


def run_capture(args: list[str]) -> str:
    result = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def decode_subprocess_bytes(data: bytes | None, encoding: str = "utf-8-sig") -> tuple[str, str]:
    if not data:
        return "", f"{encoding} with errors=replace"
    return data.decode(encoding, errors="replace"), f"{encoding} with errors=replace"


def _text_preview(text: str, limit: int = 4000) -> str:
    if not text:
        return "(empty)"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... truncated, total {len(text)} characters ..."


def _safe_resolved_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve(strict=False))
    except Exception as exc:
        return f"(could not normalize path: {exc})"


def _path_exists_text(path: Path) -> str:
    try:
        return "yes" if path.exists() else "no"
    except Exception as exc:
        return f"unknown ({exc})"


def log_ffprobe_diagnostics(
    input_path: Path,
    ffprobe: str,
    args: list[str],
    return_code: int | str | None,
    stdout_text: str,
    stderr_text: str,
    decoded_using: str,
    exc: BaseException | None = None,
) -> None:
    replacement_char = chr(0xFFFD)
    log_error("ffprobe diagnostic block begin")
    log_error(f"  input path: {input_path}")
    log_error(f"  normalized path: {_safe_resolved_path(input_path)}")
    log_error(f"  path exists: {_path_exists_text(input_path)}")
    log_error(f"  ffprobe executable path: {ffprobe}")
    log_error(f"  ffprobe command arguments: {json.dumps(args, ensure_ascii=False)}")
    log_error(f"  return code: {return_code}")
    log_error(f"  decoded using: {decoded_using}")
    log_error(f"  stdout length: {len(stdout_text)}")
    log_error(f"  stderr length: {len(stderr_text)}")
    log_error(f"  stdout replacement characters: {'yes' if replacement_char in stdout_text else 'no'}")
    log_error(f"  stderr replacement characters: {'yes' if replacement_char in stderr_text else 'no'}")
    log_error("  stdout preview:\n" + _text_preview(stdout_text))
    log_error("  stderr preview:\n" + _text_preview(stderr_text))
    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log_error("  exception traceback:\n" + tb.rstrip())
    log_error("ffprobe diagnostic block end")


def _log_file_text() -> str:
    path = log_path()
    return str(path) if path is not None else "(logging is disabled)"


def ffprobe_json(ffprobe: str, input_path: Path) -> dict[str, Any]:
    args = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(input_path),
    ]
    stdout_text = ""
    stderr_text = ""
    decoded_using = "not decoded"
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout_text, stdout_encoding = decode_subprocess_bytes(result.stdout, "utf-8-sig")
        stderr_text, stderr_encoding = decode_subprocess_bytes(result.stderr, "utf-8")
        decoded_using = f"stdout={stdout_encoding}; stderr={stderr_encoding}"
        if result.returncode != 0:
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe could not read the file. See log file: {_log_file_text()}")
        if not stdout_text.strip():
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe returned no JSON output. See log file: {_log_file_text()}")
        try:
            payload = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using, exc,
            )
            raise FFprobeError(f"ffprobe returned invalid JSON. See log file: {_log_file_text()}") from exc
        if not isinstance(payload, dict):
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe returned unexpected JSON. See log file: {_log_file_text()}")
        log_debug(
            f"ffprobe JSON decoded successfully for {input_path}; "
            f"stdout length={len(stdout_text)} stderr length={len(stderr_text)}")
        if stderr_text.strip():
            log_debug("ffprobe stderr:\n" + stderr_text.rstrip())
        return payload
    except FFprobeError:
        raise
    except Exception as exc:
        log_ffprobe_diagnostics(
            input_path, ffprobe, args, "not available",
            stdout_text, stderr_text, decoded_using, exc,
        )
        raise FFprobeError(f"ffprobe could not read the file. See log file: {_log_file_text()}") from exc


def ffprobe_full_json(ffprobe: str, input_path: Path) -> dict[str, Any]:
    args = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_program_version",
        "-show_library_versions",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-show_programs",
        str(input_path),
    ]
    stdout_text = ""
    stderr_text = ""
    decoded_using = "not decoded"
    log_debug(f"Media Info ffprobe JSON command: {json.dumps(args, ensure_ascii=False)}")
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout_text, stdout_encoding = decode_subprocess_bytes(result.stdout, "utf-8-sig")
        stderr_text, stderr_encoding = decode_subprocess_bytes(result.stderr, "utf-8")
        decoded_using = f"stdout={stdout_encoding}; stderr={stderr_encoding}"
        if result.returncode != 0:
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe could not read the file. See log file: {_log_file_text()}")
        if not stdout_text.strip():
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe returned no JSON output. See log file: {_log_file_text()}")
        try:
            payload = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using, exc,
            )
            raise FFprobeError(f"ffprobe returned invalid JSON. See log file: {_log_file_text()}") from exc
        if not isinstance(payload, dict):
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe returned unexpected JSON. See log file: {_log_file_text()}")
        log_debug(
            f"Media Info ffprobe JSON decoded for {input_path}; "
            f"stdout length={len(stdout_text)} stderr length={len(stderr_text)}"
        )
        return payload
    except FFprobeError:
        raise
    except Exception as exc:
        log_ffprobe_diagnostics(
            input_path, ffprobe, args, "not available",
            stdout_text, stderr_text, decoded_using, exc,
        )
        raise FFprobeError(f"ffprobe could not read the file. See log file: {_log_file_text()}") from exc


def ffprobe_text_overview(ffprobe: str, input_path: Path) -> str:
    args = [ffprobe, "-hide_banner", str(input_path)]
    log_debug(f"Media Info ffprobe text command: {json.dumps(args, ensure_ascii=False)}")
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout_text, _ = decode_subprocess_bytes(result.stdout, "utf-8")
        stderr_text, _ = decode_subprocess_bytes(result.stderr, "utf-8")
        overview = (stderr_text.strip() or stdout_text.strip() or "(no ffprobe text overview)")
        log_debug(
            f"Media Info ffprobe text overview returncode={result.returncode}; "
            f"length={len(overview)}"
        )
        return overview
    except Exception:
        log_exception(f"Media Info ffprobe text overview failed for {input_path}")
        return "(ffprobe text overview failed; see log file)"


def probe_packet_sizes(ffprobe: str, input_path: Path) -> dict[int, int]:
    args = [
        ffprobe,
        "-v",
        "error",
        "-show_packets",
        "-show_entries",
        "packet=stream_index,size",
        "-of",
        "csv=p=0",
        str(input_path),
    ]
    sizes: dict[int, int] = {}
    process = subprocess.Popen(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    for line in process.stdout:
        numbers = re.findall(r"\d+", line)
        if len(numbers) < 2:
            continue
        stream_index = int(numbers[0])
        packet_size = int(numbers[1])
        sizes[stream_index] = sizes.get(stream_index, 0) + packet_size
    stderr = process.stderr.read() if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        note(f"Could not calculate exact stream sizes with ffprobe packets: {stderr.strip()}")
        return {}
    return sizes


def stream_has_fast_size_metadata(stream: dict[str, Any], fmt: dict[str, Any] | None) -> bool:
    _ = fmt
    if tag_int(stream, ["NUMBER_OF_BYTES", "NUMBER_OF_BYTES-ENG"]):
        return True
    return False


def packet_size_probe_needed(answers: dict[str, Any]) -> bool:
    fmt = answers.get("format", {})
    streams = list(answers.get("video_streams", [])) + list(answers.get("audio_streams", []))
    if not streams:
        return False
    return any(not stream_has_fast_size_metadata(stream, fmt) for stream in streams)


def packet_size_probe_allowed(answers: dict[str, Any]) -> bool:
    _ = PACKET_SIZE_PROBE_MAX_BYTES
    return packet_size_probe_needed(answers)


def get_packet_sizes(answers: dict[str, Any]) -> dict[int, int]:
    if "packet_sizes" not in answers:
        if packet_size_probe_allowed(answers):
            log_debug(f"Running exact packet-size probe for {answers.get('input_path')}")
            answers["packet_sizes"] = probe_packet_sizes(answers["ffprobe"], answers["input_path"])
        else:
            reason = "stream size metadata is sufficient"
            log_debug(f"Skipping exact packet-size probe for {answers.get('input_path')}: {reason}")
            answers["packet_sizes"] = {}
    return answers["packet_sizes"]


def stream_title(stream: dict[str, Any], relative_index: int) -> str:
    codec = stream.get("codec_name", "unknown")
    channels = stream.get("channels")
    lang = display_language(stream.get("tags", {}).get("language"))
    title = stream.get("tags", {}).get("title")
    global_index = stream.get("index", "?")
    parts = [f"{relative_index}: stream #{global_index}", f"codec={codec}"]
    if channels:
        parts.append(f"channels={channels}")
    if lang:
        parts.append(f"lang={lang}")
    if title:
        parts.append(f"title={title}")
    return " | ".join(parts)


def rational_to_float(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            num_i = float(num)
            den_i = float(den)
            return None if den_i == 0 else num_i / den_i
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def bitrate_kbps(stream: dict[str, Any] | None, fmt: dict[str, Any] | None = None) -> int | None:
    for source in (stream, fmt):
        if not source:
            continue
        bit_rate = source.get("bit_rate")
        if bit_rate:
            try:
                return max(1, round(int(bit_rate) / 1000))
            except ValueError:
                pass
    return None


def format_size_bytes_from_metadata(fmt: dict[str, Any] | None) -> int | None:
    if not fmt:
        return None
    value = fmt.get("size")
    if not value:
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def format_total_bitrate_kbps(fmt: dict[str, Any] | None) -> int | None:
    direct = bitrate_kbps(None, fmt)
    if direct:
        return direct
    duration = stream_duration_seconds({}, fmt)
    size = format_size_bytes_from_metadata(fmt)
    if duration and size:
        return max(1, round(size * 8 / duration / 1000))
    return None


def stream_duration_seconds(stream: dict[str, Any], fmt: dict[str, Any] | None = None) -> float | None:
    for source in (stream, fmt):
        if not source:
            continue
        duration = source.get("duration")
        if duration:
            try:
                return float(duration)
            except ValueError:
                pass
    return None


def int_metadata_value(stream: dict[str, Any], key: str) -> int | None:
    value = stream.get(key)
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def audio_bitrate_estimate_kbps(stream: dict[str, Any]) -> int | None:
    codec = str(stream.get("codec_name", "")).lower()
    channels = int_metadata_value(stream, "channels") or 2
    sample_rate = int_metadata_value(stream, "sample_rate") or 48000
    if codec.startswith("pcm_"):
        bits = int_metadata_value(stream, "bits_per_raw_sample") or 16
        return max(1, round(sample_rate * channels * bits / 1000))
    if codec in {"flac", "alac"}:
        return max(384, channels * 384)
    if codec in {"ac3"}:
        return 192 if channels <= 2 else 448
    if codec in {"eac3"}:
        return 160 if channels <= 2 else 384
    if codec in {"opus", "libopus"}:
        return 96 if channels <= 1 else max(128, channels * 48)
    if codec in {"mp3", "mp3float", "libmp3lame"}:
        return 96 if channels <= 1 else 160
    if codec in {"aac", "aac_latm", "mp4a"}:
        return 96 if channels <= 1 else max(128, channels * 64)
    return max(96, channels * 64)


def estimate_stream_bitrate_kbps(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None = None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> int | None:
    codec_type = stream.get("codec_type")
    if codec_type == "audio":
        return audio_bitrate_estimate_kbps(stream)
    if codec_type != "video":
        return None

    total = format_total_bitrate_kbps(fmt)
    if not total:
        return None
    siblings = sibling_streams or [stream]
    unknown_video_count = 0
    known_or_estimated_other = 0
    stream_index = stream.get("index")
    for other in siblings:
        other_type = other.get("codec_type")
        direct = bitrate_kbps(other)
        if other_type == "video":
            if direct:
                known_or_estimated_other += direct
            elif other.get("index") == stream_index:
                unknown_video_count += 1
            else:
                unknown_video_count += 1
        elif other_type == "audio":
            known_or_estimated_other += direct or audio_bitrate_estimate_kbps(other) or 0

    if unknown_video_count <= 0:
        return None
    remaining = total - known_or_estimated_other
    if remaining <= 0:
        remaining = max(1, round(total * 0.85))
    return max(1, round(remaining / unknown_video_count))


def tag_int(stream: dict[str, Any], names: list[str]) -> int | None:
    tags = stream.get("tags", {})
    normalized = {str(key).upper(): value for key, value in tags.items()}
    for name in names:
        value = normalized.get(name.upper())
        if value is None:
            continue
        try:
            return int(value)
        except ValueError:
            pass
    return None


def stream_size_bytes(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None = None,
    packet_sizes: dict[int, int] | None = None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> tuple[int | None, bool]:
    exact = tag_int(stream, ["NUMBER_OF_BYTES", "NUMBER_OF_BYTES-ENG"])
    if exact:
        return exact, False

    stream_index = stream.get("index")
    if packet_sizes and stream_index in packet_sizes:
        return packet_sizes[stream_index], False

    duration = stream_duration_seconds(stream, fmt)
    rate = bitrate_kbps(stream)
    if os.environ.get("FFMWIZ_ALLOW_ESTIMATED_STREAM_SIZES") and duration and rate:
        return round(rate * 1000 * duration / 8), True
    return None, True


def stream_bitrate_kbps(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None = None,
    packet_sizes: dict[int, int] | None = None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> int | None:
    direct = bitrate_kbps(stream)
    if direct:
        return direct

    stream_index = stream.get("index")
    duration = stream_duration_seconds(stream, fmt)
    if packet_sizes and stream_index in packet_sizes and duration:
        return max(1, round(packet_sizes[stream_index] * 8 / duration / 1000))
    size, estimated = stream_size_bytes(stream, fmt, packet_sizes, sibling_streams)
    if size and duration and not estimated:
        return max(1, round(size * 8 / duration / 1000))
    return None


def format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024 * 1024):.2f} TB"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def describe_bitrate(kbps: int | None) -> str:
    return f"{kbps} kbps" if kbps else "unknown"


def describe_total_bitrate(fmt: dict[str, Any] | None) -> str:
    return describe_bitrate(format_total_bitrate_kbps(fmt))


def video_bit_depth(stream: dict[str, Any]) -> int | None:
    for key in ("bits_per_raw_sample", "bits_per_sample", "bits_per_coded_sample"):
        value = stream.get(key)
        if value not in (None, "", "0", 0):
            try:
                depth = int(value)
                if depth > 0:
                    return depth
            except (TypeError, ValueError):
                pass

    pixel_format = str(stream.get("pix_fmt") or "").lower()
    if not pixel_format:
        return None
    match = re.search(r"(?:p0?|yuv|gray|gbrp)(10|12|14|16)(?:le|be)?", pixel_format)
    if match:
        return int(match.group(1))
    if pixel_format in {"yuv420p", "yuv422p", "yuv444p", "nv12", "rgb24", "bgr24", "rgba", "bgra"}:
        return 8
    if pixel_format in {"rgba64le", "rgba64be", "rgb48le", "rgb48be"}:
        return 16
    return None


def describe_video_bit_depth(stream: dict[str, Any]) -> str:
    depth = video_bit_depth(stream)
    return f"{depth}-bit" if depth else "unknown"


def stream_metadata_value(stream: dict[str, Any], key: str, default: str = "unknown") -> str:
    value = stream.get(key)
    if value is None or value == "":
        return default
    return str(value)


def stream_tag_value(stream: dict[str, Any], key: str, default: str = "unknown") -> str:
    value = stream.get("tags", {}).get(key)
    if value is None or value == "":
        return default
    return str(value)


def display_language(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"und", "undefined", "unknown"}:
        return "unknown"
    return text


def display_color_range(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "unknown":
        return "unknown"
    normalized = text.lower()
    if normalized == "tv":
        return "TV"
    if normalized == "pc":
        return "PC"
    return text.upper() if len(text) <= 3 else text


def compatible_channel_layout(left: str, right: str) -> bool:
    if not left or not right or left == "unknown" or right == "unknown":
        return True
    return left == right


def close_enough_bitrate(left: int | None, right: int | None) -> bool:
    if not left or not right:
        return True
    return abs(left - right) <= max(8, round(max(left, right) * 0.05))


def possible_audio_duplicate(
    left: dict[str, Any],
    right: dict[str, Any],
    fmt: dict[str, Any],
    packet_sizes: dict[int, int],
) -> bool:
    if left.get("codec_name") != right.get("codec_name"):
        return False
    if stream_metadata_value(left, "sample_rate") != stream_metadata_value(right, "sample_rate"):
        return False
    if left.get("channels") != right.get("channels"):
        return False
    if not compatible_channel_layout(
        stream_metadata_value(left, "channel_layout"),
        stream_metadata_value(right, "channel_layout"),
    ):
        return False

    left_duration = stream_duration_seconds(left, fmt)
    right_duration = stream_duration_seconds(right, fmt)
    if left_duration is not None and right_duration is not None and abs(left_duration - right_duration) >= 0.5:
        return False

    left_size, _ = stream_size_bytes(left, fmt, packet_sizes)
    right_size, _ = stream_size_bytes(right, fmt, packet_sizes)
    if left_size is not None and right_size is not None:
        larger = max(left_size, right_size)
        if larger > 0 and abs(left_size - right_size) / larger >= 0.01:
            return False

    return close_enough_bitrate(
        stream_bitrate_kbps(left, fmt, packet_sizes),
        stream_bitrate_kbps(right, fmt, packet_sizes),
    )


def median_int(values: list[int]) -> int | None:
    if not values:
        return None
    sorted_values = sorted(values)
    middle = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[middle]
    return round((sorted_values[middle - 1] + sorted_values[middle]) / 2)


def classify_sparse_audio_tracks(
    audio_streams: list[dict[str, Any]],
    fmt: dict[str, Any],
    packet_sizes: dict[int, int],
) -> tuple[set[int], set[int]]:
    sizes: list[int] = []
    stream_sizes: dict[int, int] = {}
    for idx, stream in enumerate(audio_streams):
        size, _ = stream_size_bytes(stream, fmt, packet_sizes)
        if size is None:
            continue
        stream_sizes[idx] = size
        sizes.append(size)

    typical_size = max(sizes) if sizes else None
    empty_tracks: set[int] = set()
    near_empty_tracks: set[int] = set()

    for idx, stream in enumerate(audio_streams):
        size = stream_sizes.get(idx)
        duration = stream_duration_seconds(stream, fmt)
        rate = stream_bitrate_kbps(stream, fmt, packet_sizes)
        long_enough = duration is None or duration >= 10

        if size is not None and size <= EMPTY_AUDIO_MAX_BYTES:
            empty_tracks.add(idx)
            continue

        much_smaller_than_peers = (
            size is not None
            and typical_size is not None
            and typical_size > NEAR_EMPTY_AUDIO_MAX_BYTES
            and size <= max(EMPTY_AUDIO_MAX_BYTES, round(typical_size * NEAR_EMPTY_AUDIO_RATIO))
        )
        very_low_bitrate = rate is not None and rate <= NEAR_EMPTY_AUDIO_MAX_KBPS and long_enough
        tiny_long_track = size is not None and size <= 64 * 1024 and long_enough

        if much_smaller_than_peers or very_low_bitrate or tiny_long_track:
            near_empty_tracks.add(idx)

    return empty_tracks, near_empty_tracks


def audio_hash_window_starts(duration: float | None, sample_seconds: float) -> list[float]:
    if not duration or duration <= sample_seconds * 2:
        return [0.0]
    starts = {
        max(0.0, duration * 0.10),
        max(0.0, (duration - sample_seconds) * 0.50),
        max(0.0, duration - sample_seconds - max(1.0, duration * 0.05)),
    }
    return sorted(starts)


def audio_hash_segment(
    ffmpeg: str,
    input_path: Path,
    stream_index: int,
    start: float,
    sample_seconds: float,
) -> str | None:
    args = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(input_path),
        "-map",
        f"0:{stream_index}",
        "-t",
        f"{sample_seconds:.3f}",
        "-vn",
        "-sn",
        "-dn",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        "-f",
        "hash",
        "-hash",
        "md5",
        "-",
    ]
    result = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        log_warn(f"Could not hash audio stream #{stream_index}: {result.stderr.strip()}")
        return None
    match = re.search(r"MD5=([0-9a-fA-F]+)", result.stdout)
    return match.group(1).lower() if match else result.stdout.strip().lower() or None


def audio_hash(ffmpeg: str, input_path: Path, stream_index: int, duration: float | None = None) -> str | None:
    sample_seconds = max(1.0, DUPLICATE_AUDIO_HASH_SECONDS)
    starts = audio_hash_window_starts(duration, sample_seconds)
    parts: list[str] = []
    started_at = time.perf_counter()
    for start in starts:
        segment_hash = audio_hash_segment(ffmpeg, input_path, stream_index, start, sample_seconds)
        if not segment_hash:
            return None
        parts.append(segment_hash)
    log_debug(
        f"Quick audio hash stream #{stream_index}: windows={len(starts)} "
        f"seconds={sample_seconds:g} elapsed={time.perf_counter() - started_at:.3f}s"
    )
    return "|".join(parts)


def audio_hash_full(ffmpeg: str, input_path: Path, stream_index: int) -> str | None:
    args = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(input_path),
        "-map",
        f"0:{stream_index}",
        "-vn",
        "-sn",
        "-dn",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        "-f",
        "hash",
        "-hash",
        "md5",
        "-",
    ]
    result = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        log_warn(f"Could not full-hash audio stream #{stream_index}: {result.stderr.strip()}")
        return None
    match = re.search(r"MD5=([0-9a-fA-F]+)", result.stdout)
    return match.group(1).lower() if match else result.stdout.strip().lower() or None


def detect_duplicate_audio(answers: dict[str, Any]) -> dict[str, Any]:
    if "audio_duplicate_report" in answers:
        return answers["audio_duplicate_report"]

    input_path: Path = answers["input_path"]
    fmt = answers.get("format", {})
    audio_streams = answers.get("audio_streams", [])
    packet_sizes = get_packet_sizes(answers)
    possible_pairs: list[tuple[int, int]] = []
    confirmed_pairs: list[tuple[int, int]] = []
    hashes: dict[int, str | None] = {}
    sample_hashes: dict[int, str | None] = {}
    empty_tracks, near_empty_tracks = classify_sparse_audio_tracks(audio_streams, fmt, packet_sizes)
    ignored_tracks = empty_tracks | near_empty_tracks

    for left_pos in range(len(audio_streams)):
        for right_pos in range(left_pos + 1, len(audio_streams)):
            left = audio_streams[left_pos]
            right = audio_streams[right_pos]
            if left_pos in ignored_tracks or right_pos in ignored_tracks:
                continue
            if possible_audio_duplicate(left, right, fmt, packet_sizes):
                possible_pairs.append((left_pos, right_pos))

    hash_positions = sorted({pos for pair in possible_pairs for pos in pair})
    if hash_positions:
        def _sample_hash_position(pos: int) -> tuple[int, str | None]:
            stream = audio_streams[pos]
            duration = stream_duration_seconds(stream, fmt) or stream_duration_seconds({}, fmt)
            return pos, audio_hash(answers["ffmpeg"], input_path, int(stream["index"]), duration)

        max_workers = min(DUPLICATE_AUDIO_HASH_WORKERS, len(hash_positions))
        if max_workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                for pos, value in executor.map(_sample_hash_position, hash_positions):
                    sample_hashes[pos] = value
        else:
            for pos in hash_positions:
                pos, value = _sample_hash_position(pos)
                sample_hashes[pos] = value

    full_hash_positions = sorted({
        pos
        for left_pos, right_pos in possible_pairs
        if sample_hashes.get(left_pos) and sample_hashes.get(left_pos) == sample_hashes.get(right_pos)
        for pos in (left_pos, right_pos)
    })
    if full_hash_positions:
        def _full_hash_position(pos: int) -> tuple[int, str | None]:
            stream = audio_streams[pos]
            return pos, audio_hash_full(answers["ffmpeg"], input_path, int(stream["index"]))

        max_workers = min(DUPLICATE_AUDIO_HASH_WORKERS, len(full_hash_positions))
        if max_workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                for pos, value in executor.map(_full_hash_position, full_hash_positions):
                    hashes[pos] = value
        else:
            for pos in full_hash_positions:
                pos, value = _full_hash_position(pos)
                hashes[pos] = value

    for left_pos, right_pos in possible_pairs:
        if hashes.get(left_pos) and hashes.get(left_pos) == hashes.get(right_pos):
            confirmed_pairs.append((left_pos, right_pos))

    report = {
        "possible_pairs": possible_pairs,
        "confirmed_pairs": confirmed_pairs,
        "empty_tracks": empty_tracks,
        "near_empty_tracks": near_empty_tracks,
        "hashes": hashes,
        "sample_hashes": sample_hashes,
    }
    answers["audio_duplicate_report"] = report
    return report


def duplicate_labels(index: int, report: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    empty_tracks: set[int] = report.get("empty_tracks", set())
    near_empty_tracks: set[int] = report.get("near_empty_tracks", set())
    if index in empty_tracks:
        labels.append(paint("EMPTY", Color.RED))
    elif index in near_empty_tracks:
        labels.append(paint("NEAR-EMPTY", Color.NEAR_EMPTY))

    confirmed = [pair for pair in report.get("confirmed_pairs", []) if index in pair]
    possible = [pair for pair in report.get("possible_pairs", []) if index in pair and pair not in report.get("confirmed_pairs", [])]
    if confirmed:
        peers = sorted({other for pair in confirmed for other in pair if other != index})
        labels.append(paint(f"CONFIRMED duplicate of {','.join(map(str, peers))}", Color.RED))
    if possible:
        peers = sorted({other for pair in possible for other in pair if other != index})
        labels.append(paint(f"POSSIBLE duplicate of {','.join(map(str, peers))}", Color.YELLOW))
    return labels


def print_audio_duplicate_report(answers: dict[str, Any], report: dict[str, Any]) -> None:
    if not answers.get("audio_streams"):
        return
    print()
    print(paint("Audio duplicate report", Color.BOLD + Color.ORANGE))
    packet_sizes = get_packet_sizes(answers)
    fmt = answers.get("format", {})
    for idx, stream in enumerate(answers["audio_streams"]):
        size, _ = stream_size_bytes(stream, fmt, packet_sizes)
        labels = duplicate_labels(idx, report)
        suffix = f" | {' | '.join(labels)}" if labels else ""
        print(
            f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
            f"{field_text('stream', '#' + str(stream.get('index')), Color.WHITE)} | "
            f"{field_text('codec', stream_metadata_value(stream, 'codec_name'), Color.CYAN)} | "
            f"{field_text('sample_rate', stream_metadata_value(stream, 'sample_rate'), Color.GREEN)} | "
            f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
            f"{field_text('layout', stream_metadata_value(stream, 'channel_layout'), Color.WHITE)} | "
            f"{field_text('bitrate', describe_bitrate(stream_bitrate_kbps(stream, fmt, packet_sizes)), Color.YELLOW)} | "
            f"{field_text('duration', format_duration(stream_duration_seconds(stream, fmt)), Color.MAGENTA)} | "
            f"{field_text('lang', display_language(stream_tag_value(stream, 'language')), Color.WHITE)} | "
            f"{field_text('title', stream_tag_value(stream, 'title'), Color.WHITE)} | "
            f"{field_text('size', format_bytes(size), Color.LIME)}{suffix}"
        )

    possible_pairs = report.get("possible_pairs", [])
    confirmed_pairs = report.get("confirmed_pairs", [])
    empty_tracks = sorted(report.get("empty_tracks", set()))
    near_empty_tracks = sorted(report.get("near_empty_tracks", set()))
    print("  " + field_text("empty tracks", empty_tracks or "none", Color.RED if empty_tracks else Color.GREEN))
    print("  " + field_text("near-empty tracks", near_empty_tracks or "none", Color.ORANGE if near_empty_tracks else Color.GREEN))
    print("  " + field_text("possible duplicate pairs", possible_pairs or "none", Color.YELLOW if possible_pairs else Color.GREEN))
    print("  " + field_text("confirmed duplicate pairs", confirmed_pairs or "none", Color.RED if confirmed_pairs else Color.GREEN))


def duplicate_tracks_to_drop(report: dict[str, Any]) -> set[int]:
    parent: dict[int, int] = {}

    def find(value: int) -> int:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for left, right in report.get("confirmed_pairs", []):
        union(left, right)

    groups: dict[int, set[int]] = {}
    for value in list(parent):
        groups.setdefault(find(value), set()).add(value)

    drop: set[int] = set()
    for members in groups.values():
        keep = min(members)
        drop.update(member for member in members if member != keep)
    return drop


def auto_select_audio_tracks(answers: dict[str, Any], mode: str) -> list[int]:
    count = len(answers.get("audio_streams", []))
    selected = set(range(count))
    report = detect_duplicate_audio(answers)
    lowered = mode.lower()

    if "d" in lowered:
        selected -= duplicate_tracks_to_drop(report)
    if "e" in lowered:
        selected -= set(report.get("empty_tracks", set()))
        selected -= set(report.get("near_empty_tracks", set()))

    if not selected and count:
        fallback = [idx for idx in range(count) if idx not in set(report.get("empty_tracks", set()))]
        selected.add(fallback[0] if fallback else 0)
        note("Audio auto-selection would remove every track, so the safest remaining track was kept.")

    return sorted(selected)


def print_source_info(answers: dict[str, Any]) -> None:
    input_path: Path = answers["input_path"]
    fmt = answers.get("format", {})
    video_streams = answers.get("video_streams", [])
    audio_streams = answers.get("audio_streams", [])
    subtitle_streams = answers.get("subtitle_streams", [])
    file_size = input_path.stat().st_size if input_path.exists() else None
    packet_sizes = get_packet_sizes(answers)

    print()
    print(paint("Source file info", Color.BOLD + Color.LIGHT_BLUE))
    print(paint("-" * 48, Color.GRAY))
    print(field_text("Path", input_path, Color.WHITE))
    print(field_text("Container", fmt.get("format_name", "unknown"), Color.CYAN))
    print(field_text("Duration", format_duration(stream_duration_seconds({}, fmt)), Color.MAGENTA))
    print(field_text("File size", format_bytes(file_size), Color.LIME))
    print(field_text("Total bitrate", describe_total_bitrate(fmt), Color.YELLOW))

    if video_streams:
        print(paint("\nVideo streams", Color.BOLD + Color.MAGENTA))
        for idx, stream in enumerate(video_streams):
            rate = stream_bitrate_kbps(stream, fmt, packet_sizes)
            size, estimated = stream_size_bytes(stream, fmt, packet_sizes)
            duration = format_duration(stream_duration_seconds(stream, fmt))
            fps = rational_to_float(stream.get("avg_frame_rate"))
            width = stream.get("width", "?")
            height = stream.get("height", "?")
            estimate_label = " approx" if estimated and size else ""
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('size', str(width) + 'x' + str(height), Color.LIME)} | "
                f"{field_text('fps', format(fps, '.3g') if fps else 'unknown', Color.MAGENTA)} | "
                f"{field_text('bit depth', describe_video_bit_depth(stream), Color.PINK)} | "
                f"{field_text('duration', duration, Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('video-only size', format_bytes(size) + estimate_label, Color.GREEN)} | "
                f"{field_text('total bitrate', describe_total_bitrate(fmt), Color.AQUA)} | "
                f"{field_text('Color range', display_color_range(stream.get('color_range')), Color.COLOR_RANGE_VALUE)}"
            )

    if audio_streams:
        print(paint("\nAudio streams", Color.BOLD + Color.BLUE))
        report = detect_duplicate_audio(answers) if answers.get("detect_duplicate_audio", True) else None
        for idx, stream in enumerate(audio_streams):
            rate = stream_bitrate_kbps(stream, fmt, packet_sizes)
            size, estimated = stream_size_bytes(stream, fmt, packet_sizes)
            estimate_label = " approx" if estimated and size else ""
            labels = duplicate_labels(idx, report) if report else []
            label_text = f" | {' | '.join(labels)}" if labels else ""
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
                f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('track size', format_bytes(size) + estimate_label, Color.LIME)}{label_text}"
            )
        if report:
            print_audio_duplicate_report(answers, report)

    if subtitle_streams:
        print(paint("\nSubtitle streams", Color.BOLD + Color.WHITE))
        for idx, stream in enumerate(subtitle_streams):
            print(f"  {paint(str(idx), Color.LIGHT_BLUE)}: {paint(stream_title(stream, idx), Color.WHITE)}")
    print()


MEDIA_INFO_VALUE_COLORS = [
    Color.CYAN,
    Color.LIME,
    Color.MAGENTA,
    Color.YELLOW,
    Color.AQUA,
    Color.PINK,
    Color.LIGHT_BLUE,
    Color.ORANGE,
]


def media_info_color_for_key(key: str) -> str:
    normalized = str(key or "").lower().replace("-", "_").replace(" ", "_")
    if normalized == "color_range":
        return Color.COLOR_RANGE_VALUE
    total = sum(ord(ch) for ch in key)
    return MEDIA_INFO_VALUE_COLORS[total % len(MEDIA_INFO_VALUE_COLORS)]


def _plain_number_text(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    return str(value).strip()


def _as_int_value(value: Any) -> int | None:
    text = _plain_number_text(value)
    if not text or not re.fullmatch(r"[+-]?\d+(?:\.0+)?", text):
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _as_float_value(value: Any) -> float | None:
    text = _plain_number_text(value)
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _trim_float(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _plural_unit(count: int | float, singular: str, plural: str | None = None) -> str:
    return singular if float(count) == 1.0 else (plural or singular + "s")


def parse_colon_duration_seconds(value: str) -> float | None:
    match = re.fullmatch(r"(\d{1,3}):(\d{2}):(\d{2}(?:\.\d+)?)", value.strip())
    if not match:
        return None
    try:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


def media_info_seconds_text(value: Any) -> str | None:
    seconds = _as_float_value(value)
    if seconds is None:
        text = str(value).strip()
        colon_seconds = parse_colon_duration_seconds(text)
        if colon_seconds is not None:
            precision = "hh:mm:ss.fraction" if "." in text else "hh:mm:ss"
            return f"{text} ({precision}; {_trim_float(colon_seconds)} s)"
        return None
    return f"{format_duration(seconds)} ({_trim_float(seconds)} s)"


def media_info_bitrate_text(value: Any) -> str | None:
    bit_rate = _as_int_value(value)
    if bit_rate is None:
        return None
    return f"{bit_rate} bit/s ({describe_bitrate(max(1, round(bit_rate / 1000)))})"


def media_info_bytes_text(value: Any) -> str | None:
    size = _as_int_value(value)
    if size is None:
        return None
    return f"{size} B ({format_bytes(size)})"


def media_info_fps_text(value: Any) -> str | None:
    text = str(value).strip()
    fps = rational_to_float(text)
    if fps is None:
        return None
    return f"{text} fps ({format(fps, '.5g')} fps)"


def media_info_time_base_text(value: Any) -> str | None:
    text = str(value).strip()
    seconds_per_tick = rational_to_float(text)
    if seconds_per_tick is None:
        return None
    return f"{text} s/tick ({_trim_float(seconds_per_tick)} s/tick)"


def media_info_value_with_units(key: str, value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)

    key_text = str(key or "value")
    normalized = key_text.lower().replace("-", "_").replace(" ", "_")

    if normalized == "color_range":
        return display_color_range(value)

    if normalized in {"duration", "start_time", "end_time"} or (
        (normalized.startswith("duration_") or normalized.endswith("_duration"))
        and normalized != "duration_ts"
    ):
        formatted = media_info_seconds_text(value)
        if formatted:
            return formatted

    if normalized in {"time_base", "codec_time_base"}:
        formatted = media_info_time_base_text(value)
        if formatted:
            return formatted

    if normalized in {"avg_frame_rate", "r_frame_rate", "frame_rate"}:
        formatted = media_info_fps_text(value)
        if formatted:
            return formatted

    if (
        normalized in {"bit_rate", "max_bit_rate"}
        or normalized.startswith("bps")
        or normalized.endswith("_bps")
    ):
        formatted = media_info_bitrate_text(value)
        if formatted:
            return formatted

    if (
        normalized == "size"
        or "number_of_bytes" in normalized
        or normalized.endswith("_size")
        or normalized == "extradata_size"
    ):
        formatted = media_info_bytes_text(value)
        if formatted:
            return formatted

    if normalized in {"width", "height", "coded_width", "coded_height", "displaymatrix_rotation"}:
        number = _as_int_value(value)
        if number is not None:
            unit = "degree" if normalized == "displaymatrix_rotation" else "px"
            return f"{number} {unit}"

    if normalized in {"sample_rate"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} Hz"

    if normalized in {"bits_per_raw_sample", "bits_per_sample", "bits_per_coded_sample"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'bit')}"

    if normalized in {"channels"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'channel')}"

    if normalized in {"sample_aspect_ratio", "display_aspect_ratio"}:
        return f"{value} ratio"

    if normalized in {"duration_ts", "start_pts", "pts", "dts"} or normalized.endswith("_ts"):
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'tick')}"

    if normalized in {"start", "end"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'tick')}"

    if normalized in {"nb_frames", "nb_read_frames"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'frame')}"

    if "number_of_frames" in normalized:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'frame')}"

    if normalized in {"nb_read_packets"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'packet')}"

    if normalized in {"nb_streams", "stream_count"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'stream')}"

    if normalized in {"nb_programs", "program_count"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'program')}"

    if normalized in {"nb_chapters", "chapter_count"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'chapter')}"

    if normalized in {"initial_padding", "trailing_padding", "skip_samples", "frame_size"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'sample')}"

    if normalized in {"has_b_frames", "refs"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'frame')}"

    if normalized == "probe_score":
        number = _as_int_value(value)
        if number is not None:
            return f"{number}/100"

    return str(value)


def media_info_display_key(key: str) -> str:
    normalized = str(key or "").lower().replace("-", "_").replace(" ", "_")
    if normalized == "color_range":
        return "Color range"
    return key


def append_info_line(lines: list[tuple[str, str]], text: str = "", color: str = Color.WHITE) -> None:
    lines.append((text, color))


def append_info_section(lines: list[tuple[str, str]], title: str, color: str = Color.LIGHT_BLUE) -> None:
    if lines and lines[-1][0] != "":
        append_info_line(lines)
    append_info_line(lines, title, Color.BOLD + color)
    append_info_line(lines, "-" * max(48, len(title)), Color.GRAY)


def append_info_kv(lines: list[tuple[str, str]], key: str, value: Any, indent: int = 0, color: str | None = None) -> None:
    prefix = "  " * indent
    value_text = media_info_value_with_units(key, value)
    append_info_line(lines, f"{prefix}{media_info_display_key(key)}: {value_text}", color or media_info_color_for_key(key))


def append_nested_info(
    lines: list[tuple[str, str]],
    value: Any,
    indent: int = 0,
    key_name: str | None = None,
) -> None:
    if isinstance(value, dict):
        items = list(value.items())
        if key_name is not None:
            append_info_line(lines, f"{'  ' * indent}{key_name}:", Color.BOLD + media_info_color_for_key(key_name))
            indent += 1
        if not items:
            append_info_line(lines, f"{'  ' * indent}(empty)", Color.GRAY)
            return
        for key, child in items:
            append_nested_info(lines, child, indent, str(key))
        return
    if isinstance(value, list):
        if key_name is not None:
            append_info_line(lines, f"{'  ' * indent}{key_name}:", Color.BOLD + media_info_color_for_key(key_name))
            indent += 1
        if not value:
            append_info_line(lines, f"{'  ' * indent}(empty)", Color.GRAY)
            return
        for index, child in enumerate(value):
            append_nested_info(lines, child, indent, f"[{index}]")
        return
    append_info_kv(lines, key_name or "value", value, indent)


def info_stream_header(stream: dict[str, Any], relative_index: int) -> str:
    codec_type = stream.get("codec_type", "unknown")
    codec_name = stream.get("codec_name", "unknown")
    global_index = stream.get("index", "?")
    title = stream_tag_value(stream, "title", "")
    language = display_language(stream_tag_value(stream, "language", ""))
    suffix = []
    if codec_type == "video":
        suffix.append(f"bit_depth={describe_video_bit_depth(stream)}")
        suffix.append(f"Color range={display_color_range(stream.get('color_range'))}")
    if language:
        suffix.append(f"language={language}")
    if title:
        suffix.append(f"title={title}")
    suffix_text = " | " + " | ".join(suffix) if suffix else ""
    return f"Stream {relative_index} / #{global_index}: {codec_type} | codec={codec_name}{suffix_text}"


def build_media_info_report_lines(
    input_path: Path,
    payload: dict[str, Any],
    text_overview: str,
    info_path: Path,
) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    chapters = payload.get("chapters") or []
    programs = payload.get("programs") or []
    program_version = payload.get("program_version") or {}
    library_versions = payload.get("library_versions") or []

    append_info_section(lines, "Media Info Report", Color.LIGHT_BLUE)
    append_info_kv(lines, "Generated", datetime.datetime.now().isoformat(timespec="seconds"), 1, Color.WHITE)
    append_info_kv(lines, "Input path", input_path, 1, Color.WHITE)
    append_info_kv(lines, "Normalized path", _safe_resolved_path(input_path), 1, Color.CYAN)
    append_info_kv(lines, "Path exists", "yes" if input_path.exists() else "no", 1, Color.GREEN if input_path.exists() else Color.RED)
    append_info_kv(lines, "File size", format_bytes(input_path.stat().st_size if input_path.exists() else None), 1, Color.LIME)
    append_info_kv(lines, "Duration", format_duration(stream_duration_seconds({}, fmt)), 1, Color.MAGENTA)
    append_info_kv(lines, "Total bitrate", describe_total_bitrate(fmt), 1, Color.YELLOW)
    append_info_kv(lines, "Report file", info_path, 1, Color.AQUA)

    append_info_section(lines, "Container / Format", Color.CYAN)
    append_nested_info(lines, fmt, 1)

    append_info_section(lines, "Streams", Color.MAGENTA)
    append_info_kv(lines, "Stream count", len(streams), 1, Color.LIGHT_BLUE)
    type_counts: dict[str, int] = {}
    for stream in streams:
        stream_type = str(stream.get("codec_type", "unknown"))
        type_counts[stream_type] = type_counts.get(stream_type, 0) + 1
    if type_counts:
        append_info_kv(lines, "Stream types", ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items())), 1, Color.LIME)
    for relative_index, stream in enumerate(streams):
        stream_type = str(stream.get("codec_type", "unknown"))
        color = {
            "video": Color.MAGENTA,
            "audio": Color.BLUE,
            "subtitle": Color.ORANGE,
            "attachment": Color.PINK,
            "data": Color.YELLOW,
        }.get(stream_type, Color.WHITE)
        append_info_line(lines)
        append_info_line(lines, "  " + info_stream_header(stream, relative_index), Color.BOLD + color)
        append_info_line(lines, "  " + "-" * 46, Color.GRAY)
        append_nested_info(lines, stream, 2)

    append_info_section(lines, "Chapters", Color.ORANGE)
    append_info_kv(lines, "Chapter count", len(chapters), 1, Color.LIGHT_BLUE)
    append_nested_info(lines, chapters, 1)

    append_info_section(lines, "Programs", Color.YELLOW)
    append_info_kv(lines, "Program count", len(programs), 1, Color.LIGHT_BLUE)
    append_nested_info(lines, programs, 1)

    append_info_section(lines, "FFprobe Version", Color.AQUA)
    append_nested_info(lines, program_version, 1)
    append_info_line(lines)
    append_info_line(lines, "  Library versions:", Color.BOLD + Color.AQUA)
    append_nested_info(lines, library_versions, 2)

    append_info_section(lines, "FFprobe Text Overview", Color.LIME)
    for line in text_overview.splitlines() or ["(empty)"]:
        append_info_line(lines, "  " + line, Color.WHITE)
    return lines


def render_info_report(lines: list[tuple[str, str]], color: bool = True) -> str:
    rendered: list[str] = []
    for text, color_code in lines:
        rendered.append(paint(text, color_code) if color and text else text)
    return "\n".join(rendered)


def media_info_report_path(input_path: Path) -> Path:
    reports_dir = default_media_reports_dir()
    reports_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_output_stem(input_path.name)
    return unique_numbered_path(reports_dir / f"{safe_name}_info.txt")


def write_media_info_report(
    input_path: Path,
    lines: list[tuple[str, str]],
    payload: dict[str, Any],
    text_overview: str,
    info_path: Path,
) -> None:
    plain_report = render_info_report(lines, color=False)
    raw_json = json.dumps(payload, ensure_ascii=False, indent=2)
    content = (
        plain_report
        + "\n\nRaw ffprobe JSON\n"
        + "-" * 48
        + "\n"
        + raw_json
        + "\n\nRaw ffprobe text overview\n"
        + "-" * 48
        + "\n"
        + text_overview.strip()
        + "\n"
    )
    info_path.write_text(content, encoding="utf-8")
    log_info(f"Media Info report written: {info_path}")
    log_debug(f"Media Info report size: {len(content)} characters for {input_path}")


def create_media_info_report(ffprobe: str, input_path: Path) -> tuple[list[tuple[str, str]], Path]:
    started_at = time.perf_counter()
    info_path = media_info_report_path(input_path)
    payload = ffprobe_full_json(ffprobe, input_path)
    text_overview = ffprobe_text_overview(ffprobe, input_path)
    lines = build_media_info_report_lines(input_path, payload, text_overview, info_path)
    write_media_info_report(input_path, lines, payload, text_overview, info_path)
    log_info(
        f"Media Info report completed for {input_path} -> {info_path} "
        f"in {time.perf_counter() - started_at:.3f}s"
    )
    return lines, info_path


def ask_media_info_input_path(answers: dict[str, Any]) -> Path:
    while True:
        input_example = example_text(r"D:\Videos\input.mkv")
        value = ask_required(
            question_prompt(
                answers,
                "Enter file or folder path for media info",
                f"drag and drop a file/folder or paste a path; example: {input_example}",
            )
        )
        path = terminal_path(value)
        if not path.exists():
            error("Path not found. Enter the full file or folder path again.")
            continue
        return path


def media_info_folder_candidates(folder_path: Path) -> list[Path]:
    reports_dir = default_media_reports_dir()
    files = [
        path for path in folder_path.rglob("*")
        if path.is_file() and not path_is_inside(path, reports_dir)
    ]
    return sorted(files, key=lambda path: str(path.relative_to(folder_path)).lower())


def run_media_info_mode(base_answers: dict[str, Any]) -> None:
    try:
        answers = dict(base_answers)
        answers["_question_number"] = 1
        input_path = ask_media_info_input_path(answers)
        log_info(f"Media Info mode input: {input_path}")
        if input_path.is_file():
            lines, info_path = create_media_info_report(answers["ffprobe"], input_path)
            print()
            print(render_info_report(lines, color=True))
            print()
            note(f"Media info report written to: {info_path}")
            return

        candidates = media_info_folder_candidates(input_path)
        if not candidates:
            error("No files were found in this folder.")
            return
        print()
        print(paint("Media Info folder scan", Color.BOLD + Color.LIGHT_BLUE))
        print("  " + field_text("Folder", input_path, Color.WHITE))
        print("  " + field_text("Files found", len(candidates), Color.LIME))
        print("  " + field_text("Output folder", default_media_reports_dir(), Color.AQUA))
        log_info(f"Media Info folder scan: folder={input_path}; files={len(candidates)}")

        written = 0
        skipped = 0
        for index, path in enumerate(candidates, start=1):
            try:
                _, info_path = create_media_info_report(answers["ffprobe"], path)
            except FFprobeError:
                skipped += 1
                log_debug(f"Media Info skipped unsupported/unreadable file: {path}")
                continue
            except Exception:
                skipped += 1
                log_exception(f"Media Info failed for file: {path}")
                continue
            written += 1
            print(
                f"  {paint(str(index) + '.', Color.LIGHT_BLUE)} "
                f"{paint('wrote', Color.LIME)} {paint(path.name, Color.WHITE)} "
                f"{paint('->', Color.GRAY)} {paint(str(info_path), Color.AQUA)}"
            )

        print()
        if written:
            note(f"Media info reports written to: {default_media_reports_dir()}")
        if skipped:
            note(f"Skipped {skipped} unsupported or unreadable file(s). See log file: {_log_file_text()}")
        if not written:
            error("No ffprobe-readable files were found in this folder.")
    except Back:
        note("Returning to main menu.")


@dataclass
class MuxStreamInfo:
    index: int
    codec_type: str
    codec_name: str = ""
    language: str = "unknown"
    title: str = ""
    channels: int | None = None
    sample_rate: str = ""
    channel_layout: str = ""
    disposition_default: int = 0
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    duration: float | None = None
    bitrate_kbps: int | None = None
    size_bytes: int | None = None
    size_estimated: bool = False
    bit_depth: int | None = None
    color_range: str = ""

    @classmethod
    def from_ffprobe(
        cls,
        raw: dict[str, Any],
        fmt: dict[str, Any] | None = None,
        packet_sizes: dict[int, int] | None = None,
    ) -> "MuxStreamInfo":
        tags = raw.get("tags") or {}
        disposition = raw.get("disposition") or {}
        size, estimated = stream_size_bytes(raw, fmt, packet_sizes)
        return cls(
            index=int(raw.get("index", -1)),
            codec_type=str(raw.get("codec_type", "")),
            codec_name=str(raw.get("codec_name", "")),
            language=display_language(tags.get("language")),
            title=str(tags.get("title") or ""),
            channels=raw.get("channels"),
            sample_rate=stream_metadata_value(raw, "sample_rate", ""),
            channel_layout=stream_metadata_value(raw, "channel_layout", ""),
            disposition_default=int(disposition.get("default", 0) or 0),
            width=raw.get("width"),
            height=raw.get("height"),
            fps=rational_to_float(raw.get("avg_frame_rate")),
            duration=stream_duration_seconds(raw, fmt),
            bitrate_kbps=stream_bitrate_kbps(raw, fmt, packet_sizes),
            size_bytes=size,
            size_estimated=estimated,
            bit_depth=video_bit_depth(raw),
            color_range=str(raw.get("color_range") or "unknown"),
        )


@dataclass
class MuxMediaFile:
    path: Path
    streams: list[MuxStreamInfo]
    format: dict[str, Any]

    @property
    def video_streams(self) -> list[MuxStreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "video"]

    @property
    def audio_streams(self) -> list[MuxStreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "audio"]

    @property
    def subtitle_streams(self) -> list[MuxStreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "subtitle"]

    @property
    def attachment_streams(self) -> list[MuxStreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "attachment"]


@dataclass
class MuxCleanupRules:
    audio_mode: str
    audio_languages: list[str]
    audio_titles: list[str]
    audio_indexes: list[int]
    subtitle_mode: str
    subtitle_languages: list[str]
    subtitle_titles: list[str]
    subtitle_indexes: list[int]
    keep_attachments: bool
    keep_metadata: bool
    keep_chapters: bool
    overwrite: bool


def mux_find_video_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in MUX_CLEANUP_VIDEO_EXTS else []
    if not input_path.is_dir():
        return []
    return sorted(
        (path for path in input_path.rglob("*") if path.is_file() and path.suffix.lower() in MUX_CLEANUP_VIDEO_EXTS),
        key=lambda path: str(path.relative_to(input_path)).lower(),
    )


def mux_probe_file(ffprobe: str, path: Path) -> MuxMediaFile | None:
    try:
        payload = ffprobe_json(ffprobe, path)
    except FFprobeError:
        log_debug(f"Stream Cleanup Remux skipped unreadable file: {path}")
        return None
    except Exception:
        log_exception(f"Stream Cleanup Remux probe failed: {path}")
        return None
    fmt = payload.get("format", {})
    raw_streams = payload.get("streams", [])
    packet_sizes: dict[int, int] = {}
    probe_answers = {
        "input_path": path,
        "format": fmt,
        "video_streams": [stream for stream in raw_streams if stream.get("codec_type") == "video"],
        "audio_streams": [stream for stream in raw_streams if stream.get("codec_type") == "audio"],
    }
    if packet_size_probe_needed(probe_answers):
        started_at = time.perf_counter()
        packet_sizes = probe_packet_sizes(ffprobe, path)
        log_debug(
            f"Stream Cleanup packet-size probe: {path}; "
            f"streams={len(packet_sizes)}; elapsed={time.perf_counter() - started_at:.3f}s"
        )
    streams = [MuxStreamInfo.from_ffprobe(stream, fmt, packet_sizes) for stream in raw_streams]
    media = MuxMediaFile(path=path, streams=streams, format=fmt)
    log_debug(
        f"Stream Cleanup Remux probe OK: {path}; "
        f"video={len(media.video_streams)} audio={len(media.audio_streams)} "
        f"subtitle={len(media.subtitle_streams)} attachments={len(media.attachment_streams)}"
    )
    return media


def mux_scan_files(ffprobe: str, files: list[Path]) -> list[MuxMediaFile]:
    media_files: list[MuxMediaFile] = []
    for index, path in enumerate(files, start=1):
        print(
            f"{paint('[' + str(index) + '/' + str(len(files)) + ']', Color.LIGHT_BLUE)} "
            f"{paint('Scanning:', Color.CYAN)} {paint(path.name, Color.WHITE)}"
        )
        media = mux_probe_file(ffprobe, path)
        if media is not None:
            media_files.append(media)
        else:
            note(f"Skipped unreadable file: {path.name}. See log file: {_log_file_text()}")
    return media_files


def mux_format_stream(stream: MuxStreamInfo, fmt: dict[str, Any] | None = None) -> str:
    type_color = {
        "audio": Color.BLUE,
        "subtitle": Color.ORANGE,
        "video": Color.MAGENTA,
        "attachment": Color.PINK,
    }.get(stream.codec_type, Color.WHITE)
    parts = [
        field_text("index", stream.index, Color.LIGHT_BLUE),
        field_text("type", stream.codec_type, type_color),
        field_text("lang", display_language(stream.language), Color.LIME),
        field_text("title", stream.title or "-", Color.WHITE),
        field_text("codec", stream.codec_name or "-", Color.CYAN),
    ]
    if stream.codec_type == "video":
        if stream.width and stream.height:
            parts.append(field_text("size", f"{stream.width}x{stream.height}", Color.LIME))
        if stream.fps:
            parts.append(field_text("fps", format(stream.fps, ".3g"), Color.MAGENTA))
        parts.append(field_text("bit depth", f"{stream.bit_depth}-bit" if stream.bit_depth else "unknown", Color.PINK))
        parts.append(field_text("Color range", display_color_range(stream.color_range), Color.COLOR_RANGE_VALUE))
        duration = stream.duration if stream.duration is not None else stream_duration_seconds({}, fmt)
        parts.append(field_text("duration", format_duration(duration), Color.MAGENTA))
        parts.append(field_text("bitrate", describe_bitrate(stream.bitrate_kbps), Color.YELLOW))
        estimate_label = " approx" if stream.size_estimated and stream.size_bytes else ""
        parts.append(field_text("video-only size", format_bytes(stream.size_bytes) + estimate_label, Color.GREEN))
    if stream.codec_type == "audio":
        if stream.channels is not None:
            parts.append(field_text("channels", stream.channels, Color.GREEN))
        if stream.channel_layout:
            parts.append(field_text("layout", stream.channel_layout, Color.WHITE))
        if stream.sample_rate:
            parts.append(field_text("sample_rate", stream.sample_rate, Color.MAGENTA))
        duration = stream.duration if stream.duration is not None else stream_duration_seconds({}, fmt)
        parts.append(field_text("duration", format_duration(duration), Color.MAGENTA))
        parts.append(field_text("bitrate", describe_bitrate(stream.bitrate_kbps), Color.YELLOW))
        estimate_label = " approx" if stream.size_estimated and stream.size_bytes else ""
        parts.append(field_text("track size", format_bytes(stream.size_bytes) + estimate_label, Color.LIME))
    elif stream.codec_type == "subtitle":
        duration = stream.duration if stream.duration is not None else stream_duration_seconds({}, fmt)
        parts.append(field_text("duration", format_duration(duration), Color.MAGENTA))
    parts.append(field_text("default", stream.disposition_default, Color.YELLOW if stream.disposition_default else Color.GRAY))
    return " | ".join(parts)


def mux_display_path(input_root: Path, input_file: Path) -> Path:
    if input_root.is_file():
        return Path(input_file.name)
    try:
        return input_file.relative_to(input_root)
    except ValueError:
        return input_file


def mux_print_scan_report(media_files: list[MuxMediaFile], input_root: Path) -> None:
    print()
    print(paint("Stream Cleanup Scan Report", Color.BOLD + Color.AQUA))
    print(paint("=" * 72, Color.GRAY))
    for media in media_files:
        print()
        print(
            f"{paint('File:', Color.BOLD + Color.WHITE)} "
            f"{paint(str(mux_display_path(input_root, media.path)), Color.WHITE)} | "
            f"{field_text('duration', format_duration(stream_duration_seconds({}, media.format)), Color.MAGENTA)} | "
            f"{field_text('total bitrate', describe_total_bitrate(media.format), Color.YELLOW)}"
        )
        if media.video_streams:
            print(paint("  Video:", Color.BOLD + Color.MAGENTA))
            for stream in media.video_streams:
                print("    " + mux_format_stream(stream, media.format))
        else:
            print(paint("  Video: none", Color.GRAY))
        if media.audio_streams:
            print(paint("  Audio:", Color.BOLD + Color.BLUE))
            for stream in media.audio_streams:
                print("    " + mux_format_stream(stream, media.format))
        else:
            print(paint("  Audio: none", Color.GRAY))
        if media.subtitle_streams:
            print(paint("  Subtitles:", Color.BOLD + Color.ORANGE))
            for stream in media.subtitle_streams:
                print("    " + mux_format_stream(stream, media.format))
        else:
            print(paint("  Subtitles: none", Color.GRAY))
        if media.attachment_streams:
            print(paint(f"  Attachments: {len(media.attachment_streams)}", Color.PINK))


def mux_unique_stream_values(media_files: list[MuxMediaFile], codec_type: str, field: str) -> list[str]:
    values: list[str] = []
    for media in media_files:
        streams = media.audio_streams if codec_type == "audio" else media.subtitle_streams
        for stream in streams:
            value = getattr(stream, field, "")
            if value and value not in values:
                values.append(value)
    return sorted(values, key=str.lower)


def mux_stream_indexes(media_files: list[MuxMediaFile], codec_type: str) -> list[int]:
    indexes = {
        stream.index
        for media in media_files
        for stream in (media.audio_streams if codec_type == "audio" else media.subtitle_streams)
    }
    return sorted(indexes)


def mux_print_unique_summary(media_files: list[MuxMediaFile]) -> None:
    print()
    print(paint("Unique Stream Summary", Color.BOLD + Color.LIME))
    print(paint("-" * 72, Color.GRAY))
    for codec_type, color_code in (("audio", Color.BLUE), ("subtitle", Color.ORANGE)):
        print(paint(codec_type.capitalize() + " streams found:", Color.BOLD + color_code))
        summary: dict[tuple[str, str, str], int] = {}
        for media in media_files:
            streams = media.audio_streams if codec_type == "audio" else media.subtitle_streams
            for stream in streams:
                key = (display_language(stream.language), stream.title or "-", stream.codec_name or "-")
                summary[key] = summary.get(key, 0) + 1
        if not summary:
            print(paint("  none", Color.GRAY))
            continue
        for (language, title, codec), count in sorted(summary.items()):
            print(
                f"  {field_text('count', count, Color.LIME)} | "
                f"{field_text('lang', language, Color.CYAN)} | "
                f"{field_text('title', title, Color.WHITE)} | "
                f"{field_text('codec', codec, Color.MAGENTA)}"
            )
        print()


def mux_parse_csv_text(raw: str) -> list[str]:
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def mux_parse_csv_int(raw: str) -> list[int]:
    indexes: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            indexes.append(int(item))
        except ValueError:
            note(f"Ignored invalid stream index: {item}")
    return indexes


def mux_ask_choice(answers: dict[str, Any], title: str, details: str, valid: set[str], default: str) -> str:
    while True:
        value = ask_raw(question_prompt(answers, title, details, default))
        if value == "0":
            raise Back()
        if not value:
            value = default
        lowered = value.lower()
        if lowered in valid:
            return lowered
        error("Enter one of: " + ", ".join(sorted(valid)))


def mux_ask_yes_no(answers: dict[str, Any], title: str, default: bool) -> bool:
    return ask_yes_no(question_prompt(answers, title, "y/n", "y" if default else "n"), default)


def mux_ask_text(answers: dict[str, Any], title: str, details: str) -> str:
    while True:
        value = ask_raw(question_prompt(answers, title, details))
        if value == "0":
            raise Back()
        if value:
            return value
        error("This value cannot be empty.")


def mux_ask_output_base(answers: dict[str, Any], input_root: Path) -> Path:
    default_text = "Enter=input parent folder"
    while True:
        value = ask_raw(question_prompt(answers, "Enter output folder path", default_text))
        if value == "0":
            raise Back()
        if not value:
            return input_root.parent
        path = terminal_path(value)
        if path.exists() and not path.is_dir():
            error("Output path exists but is not a folder. Enter another path.")
            continue
        return path


def mux_configure_rules(answers: dict[str, Any], media_files: list[MuxMediaFile]) -> MuxCleanupRules:
    audio_languages = mux_unique_stream_values(media_files, "audio", "language")
    subtitle_languages = mux_unique_stream_values(media_files, "subtitle", "language")
    answers["_question_number"] = 2
    selection_style = mux_ask_choice(
        answers,
        "Choose stream selection style",
        "1=exact stream indexes; 2=advanced rules by language/title/index",
        {"1", "2"},
        "2",
    )

    audio_mode = "4"
    audio_language_values: list[str] = []
    audio_titles: list[str] = []
    audio_indexes: list[int] = []
    subtitle_mode = "1"
    subtitle_language_values: list[str] = []
    subtitle_titles: list[str] = []
    subtitle_indexes: list[int] = []

    if selection_style == "1":
        answers["_question_number"] = 3
        audio_value = mux_ask_text(
            answers,
            "Audio stream indexes to keep",
            f"examples: {example_text('1,2')}; all; none; available: {example_text(','.join(map(str, mux_stream_indexes(media_files, 'audio'))) or 'none')}",
        ).lower()
        if audio_value in {"all", "a", "*"}:
            audio_mode = "4"
        elif audio_value in {"none", "n", "no", "remove", "-"}:
            audio_mode = "5"
        else:
            audio_mode = "3"
            audio_indexes = mux_parse_csv_int(audio_value)

        answers["_question_number"] = 4
        subtitle_value = mux_ask_text(
            answers,
            "Subtitle stream indexes to keep",
            f"examples: {example_text('3,4')}; all; none; available: {example_text(','.join(map(str, mux_stream_indexes(media_files, 'subtitle'))) or 'none')}",
        ).lower()
        if subtitle_value in {"all", "a", "*"}:
            subtitle_mode = "5"
        elif subtitle_value in {"none", "n", "no", "remove", "-"}:
            subtitle_mode = "1"
        else:
            subtitle_mode = "4"
            subtitle_indexes = mux_parse_csv_int(subtitle_value)
    else:
        answers["_question_number"] = 3
        audio_mode = mux_ask_choice(
            answers,
            "Choose audio mode",
            f"1=by language; 2=by title; 3=by exact stream indexes; 4=keep all; 5=remove all; found languages: {example_text(','.join(audio_languages) or 'none')}",
            {"1", "2", "3", "4", "5"},
            "1",
        )
        if audio_mode == "1":
            answers["_question_number"] = 4
            audio_language_values = mux_parse_csv_text(mux_ask_text(answers, "Audio language codes to keep", "example: jpn,eng,fas"))
        elif audio_mode == "2":
            answers["_question_number"] = 4
            audio_titles = mux_parse_csv_text(mux_ask_text(answers, "Audio title text to keep", "example: japanese,commentary"))
        elif audio_mode == "3":
            answers["_question_number"] = 4
            audio_indexes = mux_parse_csv_int(mux_ask_text(answers, "Audio stream indexes to keep", "example: 2,3"))

        answers["_question_number"] = 5
        subtitle_mode = mux_ask_choice(
            answers,
            "Choose subtitle mode",
            f"1=remove all; 2=by language; 3=by title; 4=by exact stream indexes; 5=keep all; found languages: {example_text(','.join(subtitle_languages) or 'none')}",
            {"1", "2", "3", "4", "5"},
            "1",
        )
        if subtitle_mode == "2":
            answers["_question_number"] = 6
            subtitle_language_values = mux_parse_csv_text(mux_ask_text(answers, "Subtitle language codes to keep", "example: eng,fas"))
        elif subtitle_mode == "3":
            answers["_question_number"] = 6
            subtitle_titles = mux_parse_csv_text(mux_ask_text(answers, "Subtitle title text to keep", "example: signs,full"))
        elif subtitle_mode == "4":
            answers["_question_number"] = 6
            subtitle_indexes = mux_parse_csv_int(mux_ask_text(answers, "Subtitle stream indexes to keep", "example: 3,4"))

    answers["_question_number"] = 7
    if subtitle_mode == "1":
        keep_attachments = False
        note("Subtitle mode removes all subtitles, so font attachments will also be removed.")
    else:
        keep_attachments = mux_ask_yes_no(answers, "Keep MKV font attachments?", True)

    answers["_question_number"] = 8
    keep_metadata = mux_ask_yes_no(answers, "Keep input metadata?", True)
    answers["_question_number"] = 9
    keep_chapters = mux_ask_yes_no(answers, "Keep chapters?", True)
    answers["_question_number"] = 10
    overwrite = mux_ask_yes_no(answers, "Overwrite existing output files?", False)

    return MuxCleanupRules(
        audio_mode=audio_mode,
        audio_languages=audio_language_values,
        audio_titles=audio_titles,
        audio_indexes=audio_indexes,
        subtitle_mode=subtitle_mode,
        subtitle_languages=subtitle_language_values,
        subtitle_titles=subtitle_titles,
        subtitle_indexes=subtitle_indexes,
        keep_attachments=keep_attachments,
        keep_metadata=keep_metadata,
        keep_chapters=keep_chapters,
        overwrite=overwrite,
    )


def mux_text_matches_any(value: str, needles: list[str]) -> bool:
    haystack = (value or "").lower()
    return any(needle in haystack for needle in needles)


def mux_selected_audio_streams(media: MuxMediaFile, rules: MuxCleanupRules) -> list[MuxStreamInfo]:
    if rules.audio_mode == "1":
        return [stream for stream in media.audio_streams if stream.language.lower() in rules.audio_languages]
    if rules.audio_mode == "2":
        return [stream for stream in media.audio_streams if mux_text_matches_any(stream.title, rules.audio_titles)]
    if rules.audio_mode == "3":
        return [stream for stream in media.audio_streams if stream.index in rules.audio_indexes]
    if rules.audio_mode == "4":
        return media.audio_streams
    return []


def mux_selected_subtitle_streams(media: MuxMediaFile, rules: MuxCleanupRules) -> list[MuxStreamInfo]:
    if rules.subtitle_mode == "2":
        return [stream for stream in media.subtitle_streams if stream.language.lower() in rules.subtitle_languages]
    if rules.subtitle_mode == "3":
        return [stream for stream in media.subtitle_streams if mux_text_matches_any(stream.title, rules.subtitle_titles)]
    if rules.subtitle_mode == "4":
        return [stream for stream in media.subtitle_streams if stream.index in rules.subtitle_indexes]
    if rules.subtitle_mode == "5":
        return media.subtitle_streams
    return []


def mux_language_label(value: str) -> str:
    labels = {
        "ja": "JA",
        "jpn": "JA",
        "japanese": "JA",
        "en": "EN",
        "eng": "EN",
        "english": "EN",
        "fa": "FA",
        "fas": "FA",
        "per": "FA",
        "persian": "FA",
    }
    normalized = (value or "und").lower()
    return labels.get(normalized, normalized.upper())


def mux_compact_labels(values: list[str], max_items: int = 3) -> str:
    unique: list[str] = []
    for value in values:
        label = mux_language_label(value)
        if label not in unique:
            unique.append(label)
    if not unique:
        return ""
    return "+".join(unique[:max_items]) + ("+" if len(unique) > max_items else "")


def mux_stream_rule_part(kind: str, rules: MuxCleanupRules) -> str:
    if kind == "audio":
        if rules.audio_mode == "1" and rules.audio_languages:
            return f"{mux_compact_labels(rules.audio_languages)} Audio"
        if rules.audio_mode == "2" and rules.audio_titles:
            return "Selected Audio"
        if rules.audio_mode == "3" and rules.audio_indexes:
            return "Audio " + "+".join(str(index) for index in rules.audio_indexes[:4])
        if rules.audio_mode == "4":
            return "All Audio"
        if rules.audio_mode == "5":
            return "No Audio"
        return "Audio"
    if rules.subtitle_mode == "1":
        return "No Subs"
    if rules.subtitle_mode == "2" and rules.subtitle_languages:
        return f"{mux_compact_labels(rules.subtitle_languages)} Subs"
    if rules.subtitle_mode == "3" and rules.subtitle_titles:
        return "Selected Subs"
    if rules.subtitle_mode == "4" and rules.subtitle_indexes:
        return "Subs " + "+".join(str(index) for index in rules.subtitle_indexes[:4])
    if rules.subtitle_mode == "5":
        return "All Subs"
    return "Subs"


def mux_selection_suffix(rules: MuxCleanupRules) -> str:
    parts = [mux_stream_rule_part("audio", rules), mux_stream_rule_part("subtitle", rules)]
    compact: list[str] = []
    for part in parts:
        if part and part not in compact:
            compact.append(part)
    return "[" + sanitize_output_stem(" + ".join(compact) if compact else "Muxed") + "]"


def mux_unique_directory_path(path: Path) -> Path:
    if not path.exists():
        return path
    for counter in range(2, 10000):
        candidate = path.with_name(f"{path.name} ({counter})")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find available output folder for: {path}")


def mux_resolve_output_root(input_root: Path, output_base: Path, rules: MuxCleanupRules) -> Path:
    if input_root.is_dir():
        folder_name = sanitize_output_stem(f"{input_root.name} {mux_selection_suffix(rules)}")
        return mux_unique_directory_path(output_base / folder_name)
    return output_base


def mux_make_output_path(input_root: Path, output_root: Path, input_file: Path, rules: MuxCleanupRules) -> Path:
    if input_root.is_file():
        filename = sanitize_output_stem(f"{input_file.stem} {mux_selection_suffix(rules)}") + input_file.suffix
        output_file = output_root / filename
        if paths_same(output_file, input_file):
            output_file = unique_numbered_path(output_file)
        if output_file.exists() and not rules.overwrite:
            output_file = unique_numbered_path(output_file)
        return output_file
    return output_root / input_file.relative_to(input_root)


def mux_build_ffmpeg_command(
    ffmpeg: str,
    input_file: Path,
    output_file: Path,
    media: MuxMediaFile,
    rules: MuxCleanupRules,
) -> tuple[list[str], list[MuxStreamInfo], list[MuxStreamInfo]]:
    audio_keep = mux_selected_audio_streams(media, rules)
    subtitle_keep = mux_selected_subtitle_streams(media, rules)
    cmd = [ffmpeg, "-hide_banner", "-y" if rules.overwrite else "-n", "-i", str(input_file), "-map", "0:v?"]
    for stream in audio_keep:
        cmd.extend(["-map", f"0:{stream.index}"])
    for stream in subtitle_keep:
        cmd.extend(["-map", f"0:{stream.index}"])
    if rules.keep_attachments:
        cmd.extend(["-map", "0:t?"])
    cmd.extend(["-map_metadata", "0" if rules.keep_metadata else "-1"])
    cmd.extend(["-map_chapters", "0" if rules.keep_chapters else "-1"])
    cmd.extend(["-c", "copy"])
    if audio_keep:
        cmd.extend(["-disposition:a:0", "default"])
    if subtitle_keep:
        cmd.extend(["-disposition:s:0", "default"])
    cmd.append(str(output_file))
    return cmd, audio_keep, subtitle_keep


def mux_print_confirm(input_root: Path, output_base: Path, output_root: Path, rules: MuxCleanupRules) -> None:
    print()
    print(paint("Confirm Stream Cleanup Remux", Color.BOLD + Color.LIGHT_BLUE))
    print(paint("-" * 72, Color.GRAY))
    print("  " + field_text("Input", input_root, Color.WHITE))
    print("  " + field_text("Output base", output_base, Color.CYAN))
    print("  " + field_text("Output root", output_root, Color.LIME))
    print("  " + field_text("Audio mode", rules.audio_mode, Color.BLUE))
    print("  " + field_text("Audio languages", rules.audio_languages or "-", Color.CYAN))
    print("  " + field_text("Audio titles", rules.audio_titles or "-", Color.WHITE))
    print("  " + field_text("Audio indexes", rules.audio_indexes or "-", Color.LIGHT_BLUE))
    print("  " + field_text("Subtitle mode", rules.subtitle_mode, Color.ORANGE))
    print("  " + field_text("Subtitle languages", rules.subtitle_languages or "-", Color.CYAN))
    print("  " + field_text("Subtitle titles", rules.subtitle_titles or "-", Color.WHITE))
    print("  " + field_text("Subtitle indexes", rules.subtitle_indexes or "-", Color.LIGHT_BLUE))
    print("  " + field_text("Keep attachments", rules.keep_attachments, Color.PINK))
    print("  " + field_text("Keep metadata", rules.keep_metadata, Color.GREEN))
    print("  " + field_text("Keep chapters", rules.keep_chapters, Color.GREEN))
    print("  " + field_text("Overwrite", rules.overwrite, Color.RED if rules.overwrite else Color.GREEN))


def mux_process_files(
    ffmpeg: str,
    media_files: list[MuxMediaFile],
    input_root: Path,
    output_root: Path,
    rules: MuxCleanupRules,
) -> tuple[int, float]:
    print()
    print(paint("Processing Stream Cleanup Remux", Color.BOLD + Color.LIGHT_BLUE))
    print(paint("=" * 72, Color.GRAY))
    started_at = time.perf_counter()
    total = len(media_files)
    succeeded = 0
    skipped = 0
    failed = 0
    output_root.mkdir(parents=True, exist_ok=True)
    log_info(f"Stream Cleanup Remux processing started: input={input_root}; output={output_root}; files={total}; rules={rules}")
    for index, media in enumerate(media_files, start=1):
        output_file = mux_make_output_path(input_root, output_root, media.path, rules)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        rel = mux_display_path(input_root, media.path)
        if output_file.exists() and not rules.overwrite:
            skipped += 1
            note(f"[{index}/{total}] Skip existing output: {rel}")
            log_warn(f"Stream Cleanup Remux skip existing output: {output_file}")
            continue
        cmd, audio_keep, subtitle_keep = mux_build_ffmpeg_command(ffmpeg, media.path, output_file, media, rules)
        log_info(
            f"Stream Cleanup Remux file {index}/{total}: input={media.path}; output={output_file}; "
            f"audio_keep={[s.index for s in audio_keep]}; subtitle_keep={[s.index for s in subtitle_keep]}; "
            f"attachments={rules.keep_attachments}"
        )
        if not media.video_streams:
            note(f"[{index}/{total}] Warning: no video stream found: {rel}")
        if rules.audio_mode != "5" and not audio_keep:
            note(f"[{index}/{total}] Warning: no matching audio selected: {rel}")
        print()
        print(
            f"{paint('[' + str(index) + '/' + str(total) + ']', Color.LIGHT_BLUE)} "
            f"{paint('Remuxing:', Color.CYAN)} {paint(str(rel), Color.WHITE)}"
        )
        print(
            "  "
            + field_text("audio kept", len(audio_keep), Color.BLUE)
            + " | "
            + field_text("subtitles kept", len(subtitle_keep), Color.ORANGE)
            + " | "
            + field_text("attachments", "yes" if rules.keep_attachments else "no", Color.PINK)
        )
        duration = stream_duration_seconds({}, media.format) or None
        rc, _elapsed = run_ffmpeg_with_progress(cmd, total_duration=duration, label="Stream Cleanup Remux")
        if rc == 0:
            succeeded += 1
            note(f"OK: {output_file}")
        else:
            failed += 1
            error(f"FAILED: {media.path}. See log file: {_log_file_text()}")
    elapsed = time.perf_counter() - started_at
    print()
    print(paint("Stream Cleanup Remux Done", Color.BOLD + Color.LIME))
    print("  " + field_text("Total", total, Color.WHITE))
    print("  " + field_text("OK", succeeded, Color.GREEN))
    print("  " + field_text("Skipped", skipped, Color.YELLOW))
    print("  " + field_text("Failed", failed, Color.RED if failed else Color.GREEN))
    print("  " + field_text("Output", output_root, Color.AQUA))
    print("  " + field_text("Total time elapsed", format_elapsed(elapsed), Color.MAGENTA))
    log_info(
        f"Stream Cleanup Remux done: total={total}; ok={succeeded}; skipped={skipped}; "
        f"failed={failed}; elapsed={format_elapsed(elapsed)}; output={output_root}"
    )
    return (1 if failed else 0), elapsed


def mux_verify_output(ffprobe: str, root: Path) -> None:
    files = mux_find_video_files(root)
    print()
    print(paint("Verify Stream Cleanup Output", Color.BOLD + Color.LIGHT_BLUE))
    print(paint("-" * 72, Color.GRAY))
    if not files:
        note("No supported video files found.")
        return
    for path in files:
        media = mux_probe_file(ffprobe, path)
        if media is None:
            continue
        audio_langs = ",".join(stream.language for stream in media.audio_streams) or "-"
        subtitle_langs = ",".join(stream.language for stream in media.subtitle_streams) or "-"
        print(
            f"{paint(str(mux_display_path(root, path)), Color.WHITE)} | "
            f"{field_text('video', len(media.video_streams), Color.MAGENTA)} | "
            f"{field_text('audio', len(media.audio_streams), Color.BLUE)} "
            f"{paint('[' + audio_langs + ']', Color.CYAN)} | "
            f"{field_text('subs', len(media.subtitle_streams), Color.ORANGE)} "
            f"{paint('[' + subtitle_langs + ']', Color.CYAN)} | "
            f"{field_text('attachments', len(media.attachment_streams), Color.PINK)}"
        )


def ask_mux_cleanup_input_path(answers: dict[str, Any]) -> Path:
    while True:
        value = ask_required(
            question_prompt(
                answers,
                "Enter file or folder path for Stream Cleanup Remux",
                f"drag/drop a video file or folder; supported: {option_list(sorted(ext.lstrip('.') for ext in MUX_CLEANUP_VIDEO_EXTS))}",
            )
        )
        path = terminal_path(value)
        if not path.exists():
            error("Path not found. Enter a valid file or folder path.")
            continue
        return path


def run_mux_cleanup_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_mux_cleanup_mode_impl(base_answers)
    except Back:
        note("Returning to main menu.")
        return None


def _run_mux_cleanup_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    while True:
        answers["_question_number"] = 1
        input_root = ask_mux_cleanup_input_path(answers)
        log_info(f"Stream Cleanup Remux input: {input_root}")
        files = mux_find_video_files(input_root)
        if not files:
            error("No supported video files were found.")
            return None
        note(f"Found {len(files)} supported video file(s).")
        media_files = mux_scan_files(answers["ffprobe"], files)
        if not media_files:
            error("No files could be scanned successfully.")
            return None
        mux_print_scan_report(media_files, input_root)
        mux_print_unique_summary(media_files)

        action = None
        while True:
            answers["_question_number"] = 2
            try:
                action = mux_ask_choice(
                    answers,
                    "Choose Stream Cleanup action",
                    "1=process files; 2=scan only; 3=verify another folder",
                    {"1", "2", "3"},
                    "1",
                )
            except Back:
                break
            if action == "2":
                note("Stream Cleanup scan only completed.")
                return None
            if action == "3":
                try:
                    answers["_question_number"] = 3
                    verify_path = ask_mux_cleanup_input_path(answers)
                except Back:
                    continue
                mux_verify_output(answers["ffprobe"], verify_path)
                return None
            break
        if action == "1":
            break

    while True:
        try:
            rules = mux_configure_rules(answers, media_files)
            answers["_question_number"] = 11
            output_base = mux_ask_output_base(answers, input_root)
            output_root = mux_resolve_output_root(input_root, output_base, rules)
            mux_print_confirm(input_root, output_base, output_root, rules)
            answers["_question_number"] = 12
            if not mux_ask_yes_no(answers, "Start Stream Cleanup Remux now?", True):
                note("Stream Cleanup Remux was not started.")
                return None
            break
        except Back:
            note("Back. Returning to stream selection.")
            continue
    result = mux_process_files(answers["ffmpeg"], media_files, input_root, output_root, rules)
    try:
        answers["_question_number"] = 13
        if mux_ask_yes_no(answers, "Verify output folder now?", True):
            mux_verify_output(answers["ffprobe"], output_root)
    except Back:
        note("Verification skipped.")
    return result


FOLDER_MEDIA_METADATA_KEYS = (
    "input_path",
    "probe",
    "format",
    "video_streams",
    "audio_streams",
    "subtitle_streams",
    "packet_sizes",
)


def is_folder_media_candidate(path: Path) -> bool:
    return path.is_file() and path.suffix.lower().lstrip(".") in FOLDER_MEDIA_EXTS


def path_is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def folder_item_display_name(item: dict[str, Any]) -> str:
    return str(item.get("relative_path") or item["path"].name)


def copy_media_metadata(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in FOLDER_MEDIA_METADATA_KEYS:
        if key in source:
            target[key] = source[key]
        else:
            target.pop(key, None)
    target.pop("audio_duplicate_report", None)
    target.pop("final_resolution", None)
    target.pop("crop_box_dimensions", None)
    target.pop("cropped_aspect_ratio", None)


def scan_folder_media_files(
    base_answers: dict[str, Any],
    folder_path: Path,
    exclude_folder: Path | None = None,
) -> list[dict[str, Any]]:
    files = [
        path for path in folder_path.rglob("*")
        if path.is_file() and not (exclude_folder and path_is_inside(path, exclude_folder))
    ]
    candidates = sorted(
        (path for path in files if is_folder_media_candidate(path)),
        key=lambda path: str(path.relative_to(folder_path)).lower(),
    )
    skipped_non_media = sum(1 for path in files if not is_folder_media_candidate(path))
    log_info(
        f"Folder Encode scan: folder={folder_path}; media candidates={len(candidates)}; "
        f"non-media files ignored={skipped_non_media}"
    )
    def probe_one(path: Path) -> dict[str, Any] | None:
        probe_answers = dict(base_answers)
        probe_answers["detect_duplicate_audio"] = False
        try:
            load_input_metadata(probe_answers, path)
        except Exception:
            log_exception(f"Folder Encode skipped file after probe failure: {path}")
            note(f"Skipped unreadable media file: {path.name}. See log file: {_log_file_text()}")
            return None
        return {
            "path": path,
            "relative_path": path.relative_to(folder_path),
            "answers": {key: probe_answers.get(key) for key in FOLDER_MEDIA_METADATA_KEYS if key in probe_answers},
            "has_video": bool(probe_answers.get("video_streams")),
            "has_audio": bool(probe_answers.get("audio_streams")),
        }

    items: list[dict[str, Any]] = []
    max_workers = min(FOLDER_PROBE_WORKERS, len(candidates)) if candidates else 1
    if max_workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for item in executor.map(probe_one, candidates):
                if item is not None:
                    items.append(item)
    else:
        for path in candidates:
            item = probe_one(path)
            if item is not None:
                items.append(item)
    return items


def folder_audio_issue_summary(filename: str, report: dict[str, Any]) -> str | None:
    parts: list[str] = []
    empty_tracks = sorted(report.get("empty_tracks", set()))
    near_empty_tracks = sorted(report.get("near_empty_tracks", set()))
    confirmed_pairs = report.get("confirmed_pairs", [])
    possible_pairs = report.get("possible_pairs", [])
    if empty_tracks:
        parts.append(f"empty audio tracks {empty_tracks}")
    if near_empty_tracks:
        parts.append(f"near-empty audio tracks {near_empty_tracks}")
    if confirmed_pairs:
        parts.append(f"confirmed duplicate audio pairs {confirmed_pairs}")
    if possible_pairs and not confirmed_pairs:
        parts.append(f"possible duplicate audio pairs {possible_pairs}")
    if not parts:
        return None
    return f"{filename}: " + "; ".join(parts)


def ensure_folder_exact_packet_sizes(items: list[dict[str, Any]], base_answers: dict[str, Any]) -> None:
    work: list[dict[str, Any]] = []
    for item in items:
        item_answers = dict(base_answers)
        copy_media_metadata(item_answers, item["answers"])
        if "packet_sizes" not in item["answers"] and packet_size_probe_needed(item_answers):
            work.append(item)
    if not work:
        return

    def probe_item(item: dict[str, Any]) -> tuple[dict[str, Any], dict[int, int]]:
        item_answers = dict(base_answers)
        copy_media_metadata(item_answers, item["answers"])
        started_at = time.perf_counter()
        sizes = probe_packet_sizes(item_answers["ffprobe"], item_answers["input_path"])
        log_debug(
            f"Folder exact packet-size probe completed for {item_answers['input_path']} "
            f"in {time.perf_counter() - started_at:.3f}s"
        )
        return item, sizes

    max_workers = min(FOLDER_PROBE_WORKERS, len(work))
    if max_workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for item, sizes in executor.map(probe_item, work):
                item["answers"]["packet_sizes"] = sizes
    else:
        for item in work:
            _, sizes = probe_item(item)
            item["answers"]["packet_sizes"] = sizes


def print_folder_media_summary(items: list[dict[str, Any]], base_answers: dict[str, Any]) -> None:
    print()
    print(paint("Folder media files", Color.BOLD + Color.LIGHT_BLUE))
    print(paint("-" * 48, Color.GRAY))
    ensure_folder_exact_packet_sizes(items, base_answers)
    audio_warnings: list[str] = []
    for index, item in enumerate(items, start=1):
        detail_answers = dict(base_answers)
        copy_media_metadata(detail_answers, item["answers"])
        detail_answers["detect_duplicate_audio"] = base_answers.get("detect_duplicate_audio", True)
        detail_answers["_quiet_packet_size_probe"] = True
        input_path: Path = detail_answers["input_path"]
        display_name = folder_item_display_name(item)
        fmt = detail_answers.get("format", {})
        packet_sizes = get_packet_sizes(detail_answers)
        duration = format_duration(stream_duration_seconds({}, fmt))
        print(
            f"  {paint(str(index) + '.', Color.LIGHT_BLUE)} "
            f"{paint(display_name, Color.WHITE)} | "
            f"{field_text('duration', duration, Color.MAGENTA)} | "
            f"{field_text('total bitrate', describe_total_bitrate(fmt), Color.YELLOW)}"
        )

        for video_idx, stream in enumerate(detail_answers.get("video_streams", [])):
            rate = stream_bitrate_kbps(stream, fmt, packet_sizes)
            size, estimated = stream_size_bytes(stream, fmt, packet_sizes)
            stream_duration = format_duration(stream_duration_seconds(stream, fmt))
            fps = rational_to_float(stream.get("avg_frame_rate"))
            width = stream.get("width", "?")
            height = stream.get("height", "?")
            estimate_label = " approx" if estimated and size else ""
            print(
                f"     {paint('video ' + str(video_idx), Color.MAGENTA)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('size', str(width) + 'x' + str(height), Color.LIME)} | "
                f"{field_text('fps', format(fps, '.3g') if fps else 'unknown', Color.MAGENTA)} | "
                f"{field_text('bit depth', describe_video_bit_depth(stream), Color.PINK)} | "
                f"{field_text('Color range', display_color_range(stream.get('color_range')), Color.COLOR_RANGE_VALUE)} | "
                f"{field_text('duration', stream_duration, Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('video-only size', format_bytes(size) + estimate_label, Color.GREEN)} | "
                f"{field_text('total bitrate', describe_total_bitrate(fmt), Color.AQUA)}"
            )

        for audio_idx, stream in enumerate(detail_answers.get("audio_streams", [])):
            rate = stream_bitrate_kbps(stream, fmt, packet_sizes)
            size, estimated = stream_size_bytes(stream, fmt, packet_sizes)
            estimate_label = " approx" if estimated and size else ""
            print(
                f"     {paint('audio ' + str(audio_idx), Color.BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('track size', format_bytes(size) + estimate_label, Color.LIME)}"
            )

        if detail_answers.get("audio_streams") and detail_answers.get("detect_duplicate_audio", True):
            report = detect_duplicate_audio(detail_answers)
            warning = folder_audio_issue_summary(display_name, report)
            if warning:
                audio_warnings.append(warning)
        if index < len(items):
            print()
    if audio_warnings:
        print()
        print(paint("Audio track warnings", Color.BOLD + Color.ORANGE))
        for warning in audio_warnings:
            print("  " + paint(warning, Color.ORANGE))
    print()


def choose_folder_representative(items: list[dict[str, Any]]) -> dict[str, Any]:
    for item in items:
        if item.get("has_video") and item.get("has_audio"):
            return item
    for item in items:
        if item.get("has_video"):
            return item
    return items[0]


def folder_default_output_path(input_folder: Path) -> Path:
    return input_folder.parent / f"{sanitize_output_stem(input_folder.name)}_Encode"


def validate_stream_selection_for_folder_job(
    selected: list[int] | str,
    count: int,
    stream_kind: str,
) -> None:
    if selected == "all":
        return
    bad = [index for index in selected if index < 0 or index >= count]
    if bad:
        raise ValueError(
            f"Selected {stream_kind} stream(s) {bad} do not exist in this file. "
            f"Available range: 0 to {max(0, count - 1)}."
        )


def prepare_folder_job_answers(
    settings_answers: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    job = dict(settings_answers)
    copy_media_metadata(job, item["answers"])
    job["output_location"] = settings_answers["folder_output_location"]
    job["output_collision_suffix"] = "_Encode"
    job.pop("output_name_stem", None)
    job.pop("output_path", None)
    job.pop("cmd", None)

    if settings_answers.get("output_format_keep_input"):
        input_ext = job["input_path"].suffix.lstrip(".") or ("mp4" if job.get("video_streams") else "mp3")
        job["output_ext"] = input_ext.lower()

    if output_has_video(job):
        if job.get("video_bitrate_keep"):
            packet_sizes = get_packet_sizes(job)
            source = stream_bitrate_kbps(job["video_streams"][0], job.get("format"), packet_sizes)
            job["video_bitrate_kbps"] = source
        if job.get("cut_keep_ranges"):
            duration = stream_duration_seconds({}, job.get("format")) or 0.0
            if duration > 0:
                keep_ranges = normalize_cut_ranges(list(job.get("cut_keep_ranges") or []), duration)
                if keep_ranges:
                    job["cut_keep_ranges"] = keep_ranges
                else:
                    job.pop("cut_keep_ranges", None)
                    note(f"Cuts skipped for {job['input_path'].name}: no valid ranges remain after clamping to this file.")
    else:
        job.pop("cut_keep_ranges", None)

    if not job.get("audio_streams"):
        job["audio_tracks"] = []
    elif job.get("audio_tracks_mode") in {"d", "e", "de", "ed"}:
        job["audio_tracks"] = auto_select_audio_tracks(job, str(job["audio_tracks_mode"]))
    else:
        selected_audio = job.get("audio_tracks", [0])
        validate_stream_selection_for_folder_job(selected_audio, len(job["audio_streams"]), "audio")

    if job.get("subtitle_tracks") is not None:
        if not job.get("subtitle_streams"):
            job["subtitle_tracks"] = []
        else:
            validate_stream_selection_for_folder_job(
                job.get("subtitle_tracks", []),
                len(job["subtitle_streams"]),
                "subtitle",
            )

    audio_indices = selected_audio_streams(job) if job.get("audio_streams") else []
    if audio_indices and job.get("audio_bitrate_keep") and job.get("audio_codec") != "copy":
        first_selected = audio_indices[0]
        packet_sizes = get_packet_sizes(job)
        source = stream_bitrate_kbps(job["audio_streams"][first_selected], job.get("format"), packet_sizes)
        job["audio_bitrate_kbps"] = source

    if output_is_audio_only(job) and not audio_indices:
        raise ValueError("Audio-only output was selected, but this file has no selected audio stream.")
    if not output_has_video(job) and not audio_indices:
        raise ValueError("No output streams are selected for this file.")

    return job


def list_muxers(ffmpeg: str) -> list[str]:
    try:
        output = run_capture([ffmpeg, "-hide_banner", "-muxers"])
    except Exception:
        return ["mp4", "mkv", "mov", "webm", "mp3", "m4a", "wav", "flac", "opus"]

    muxers: list[str] = []
    for line in output.splitlines():
        line = line.rstrip()
        match = re.match(r"\s*E\s+([^\s]+)", line)
        if match:
            muxers.extend(name for name in match.group(1).split(",") if name)
    return sorted(set(muxers))


def list_encoders(ffmpeg: str, kind: str) -> list[str]:
    try:
        output = run_capture([ffmpeg, "-hide_banner", "-encoders"])
    except Exception:
        if kind == "video":
            return ["hevc_nvenc", "h264_nvenc", "av1_nvenc", "libx265", "libx264", "libaom-av1"]
        return ["aac", "libopus", "libmp3lame", "flac"]

    wanted = "V" if kind == "video" else "A"
    encoders: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] != wanted:
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            encoders.append(parts[1])
    return sorted(set(encoders))


def options_text(items: list[str], extra: list[str] | None = None) -> str:
    values = []
    if extra:
        values.extend(extra)
    values.extend(items)
    return ",".join(dict.fromkeys(values))


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def default_config_path() -> Path:
    return script_dir() / CONFIG_FILE_NAME


def default_launcher_path() -> Path:
    return script_dir() / LAUNCHER_FILE_NAME


def asset_path(*parts: str) -> Path:
    return script_dir().joinpath(ASSET_DIR_NAME, *parts)


def ensure_config_file(path: Path) -> None:
    if path.exists():
        return
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    note(f"Created config file: {path}")


def launcher_content() -> str:
    script_name = Path(__file__).name
    return f"""param(
    [Parameter(ValueFromRemainingArguments = $true)]
    $ScriptArgs
)

$ErrorActionPreference = 'Stop'
$scriptDir = if ($PSScriptRoot) {{ $PSScriptRoot }} else {{ Split-Path -Parent $PSCommandPath }}
$scriptPath = Join-Path -Path $scriptDir -ChildPath '{script_name}'

if (-not (Test-Path -LiteralPath $scriptPath)) {{
    Write-Host "Script file was not found: $scriptPath" -ForegroundColor Red
    exit 1
}}

$forwarded = @()
if ($ScriptArgs) {{ $forwarded = @($ScriptArgs) }}

$pythonExitCode = 9009
$pythonExe = $null
$pythonArgs = @()
function Test-FFmWizPython {{
    param(
        [string]$Exe,
        [string[]]$Args
    )
    $oldErrorActionPreference = $ErrorActionPreference
    try {{
        $ErrorActionPreference = 'Continue'
        & $Exe @Args -c "import sys" > $null 2>&1
        return $LASTEXITCODE -eq 0
    }} catch {{
        return $false
    }} finally {{
        $ErrorActionPreference = $oldErrorActionPreference
    }}
}}
if (Get-Command py -ErrorAction SilentlyContinue) {{
    if (Test-FFmWizPython -Exe 'py' -Args @('-3')) {{
        $pythonExe = 'py'
        $pythonArgs = @('-3')
    }}
}}
if (-not $pythonExe -and (Get-Command python -ErrorAction SilentlyContinue)) {{
    if (Test-FFmWizPython -Exe 'python' -Args @()) {{
        $pythonExe = 'python'
        $pythonArgs = @()
    }}
}}
if (-not $pythonExe -and (Get-Command python3 -ErrorAction SilentlyContinue)) {{
    if (Test-FFmWizPython -Exe 'python3' -Args @()) {{
        $pythonExe = 'python3'
        $pythonArgs = @()
    }}
}}
if ($pythonExe) {{
    & $pythonExe @pythonArgs $scriptPath @forwarded
    $pythonExitCode = if ($null -ne $LASTEXITCODE) {{ $LASTEXITCODE }} else {{ 0 }}
}} else {{
    Write-Host 'Python was not found in PATH.' -ForegroundColor Red
}}

exit $pythonExitCode
"""


def ensure_launcher_file(path: Path) -> None:
    content = launcher_content()
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8-sig")
            if existing == content:
                return
        except UnicodeDecodeError:
            existing = path.read_text(encoding="utf-8", errors="replace")
            if existing == content:
                return
        log_warn(f"Existing launcher file differs from the FFmWiz template; leaving it unchanged: {path}")
        return
    path.write_text(content, encoding="utf-8")
    note(f"Created launcher file: {path}")


# ------------------------------------------------------------------
# FFmpeg capability reference generator. Captures the output of the
# main `ffmpeg -<list>` commands into a plain-text reference file
# beside this script. Available codecs/encoders/etc. are build-
# specific, so the only authoritative list is the one produced by the
# user's own ffmpeg.exe.
# ------------------------------------------------------------------

FFMPEG_REFERENCE_SECTIONS: list[tuple[str, list[str]]] = [
    ("FFmpeg version + build configuration", ["-hide_banner", "-version"]),
    ("Supported formats (container names)", ["-hide_banner", "-formats"]),
    ("Output muxers", ["-hide_banner", "-muxers"]),
    ("Input demuxers", ["-hide_banner", "-demuxers"]),
    ("Codecs (all)", ["-hide_banner", "-codecs"]),
    ("Encoders", ["-hide_banner", "-encoders"]),
    ("Decoders", ["-hide_banner", "-decoders"]),
    ("Bitstream filters", ["-hide_banner", "-bsfs"]),
    ("Filters", ["-hide_banner", "-filters"]),
    ("Protocols", ["-hide_banner", "-protocols"]),
    ("Hardware acceleration methods", ["-hide_banner", "-hwaccels"]),
    ("Pixel formats", ["-hide_banner", "-pix_fmts"]),
    ("Sample (audio) formats", ["-hide_banner", "-sample_fmts"]),
    ("Channel layouts", ["-hide_banner", "-layouts"]),
    ("Colors / color spaces", ["-hide_banner", "-colors"]),
]


def generate_ffmpeg_reference_text(ffmpeg_path: str) -> str:
    """Run ffmpeg's various -list commands and return a single combined
    plain-text reference. Each section captures the live output from the
    installed ffmpeg.exe so the reference is always accurate for THIS build."""
    header = [
        "============================================================",
        " FFmWiz - FFmpeg capability reference",
        "============================================================",
        " This file was auto-generated from your installed ffmpeg.exe.",
        f" ffmpeg binary: {ffmpeg_path}",
        " Delete this file (or pass --refresh-ffmpeg-reference to the",
        " launcher) to regenerate it.",
        "",
        " Notes:",
        "   - FFmpeg support is build-specific. Codecs, encoders,",
        "     muxers, filters, protocols, and hwaccels listed here",
        "     reflect what THIS ffmpeg binary supports - nothing more.",
        "   - To inspect any single list interactively, run e.g.:",
        "       ffmpeg -hide_banner -encoders",
        "       ffmpeg -hide_banner -decoders",
        "       ffmpeg -hide_banner -hwaccels",
        "       ffmpeg -h encoder=hevc_nvenc",
        "       ffmpeg -h muxer=mp4",
        "============================================================",
        "",
    ]
    parts: list[str] = ["\n".join(header)]
    for title, args in FFMPEG_REFERENCE_SECTIONS:
        parts.append("\n" + "-" * 60)
        parts.append(f" {title}")
        parts.append(" Command: ffmpeg " + " ".join(args))
        parts.append("-" * 60 + "\n")
        try:
            result = subprocess.run(
                [ffmpeg_path, *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            output = result.stdout.strip() or "(no output)"
        except Exception as exc:
            output = f"(failed to run: {exc})"
        parts.append(output)
        parts.append("")
    return "\n".join(parts)


def default_ffmpeg_reference_path() -> Path:
    return script_dir() / FFMPEG_REFERENCE_FILE_NAME


def default_media_reports_dir() -> Path:
    return script_dir() / MEDIA_REPORTS_DIR_NAME


def ensure_ffmpeg_reference_file(path: Path, ffmpeg_path: str, force: bool = False) -> None:
    """Create the FFmpeg capability reference text file next to the script if
    it does not already exist. Pass force=True to regenerate."""
    if path.exists() and not force:
        return
    try:
        content = generate_ffmpeg_reference_text(ffmpeg_path)
        path.write_text(content, encoding="utf-8")
        note(f"Generated FFmpeg capability reference: {path}")
    except OSError as exc:
        note(f"Could not write FFmpeg reference next to the script: {exc}")


def config_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = config.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("config.json must contain a settings object.")
    return settings


def config_value(config: dict[str, Any], key: str, fallback: str = "") -> str:
    value = config_settings(config).get(key, fallback)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "y" if value else "n"
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return strip_quotes(str(value).strip())


def parse_bool_config(value: str, default: bool) -> bool:
    if not value:
        return default
    lowered = value.lower()
    if lowered in {"y", "yes", "true", "1", "on"}:
        return True
    if lowered in {"n", "no", "false", "0", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def parse_int_config(value: str, default: int | None = None, allow_n: bool = True) -> int | str | None:
    if not value:
        return default
    lowered = value.lower()
    if lowered == "n" and allow_n:
        return "n"
    if not re.fullmatch(r"\d+", value):
        raise ValueError(f"Invalid integer value: {value}")
    return int(value)


def parse_selection_config(value: str, max_count: int, default: list[int], allow_none: bool = False) -> list[int] | str:
    lowered = value.lower().strip()
    if not lowered:
        return default
    if lowered in {"n", "all"}:
        return "all"
    if allow_none and lowered in {"none", "no", "clear", "delete"}:
        return []
    pieces = [piece.strip() for piece in lowered.split(",") if piece.strip()]
    if any(not re.fullmatch(r"\d+", piece) for piece in pieces):
        raise ValueError(f"Invalid stream selection: {value}")
    numbers = sorted(set(int(piece) for piece in pieces))
    bad = [number for number in numbers if number < 0 or number >= max_count]
    if bad:
        raise ValueError(f"Invalid stream number(s): {bad}. Allowed range: 0 to {max_count - 1}")
    return numbers


def ask_raw(prompt: str) -> str:
    value = strip_quotes(input(prompt).strip())
    if value.lower() == "exit":
        raise ExitWizard()
    return value


def ask_required(prompt: str, allow_n: bool = False) -> str:
    while True:
        value = ask_raw(prompt)
        if value == "0":
            raise Back()
        if value:
            if value.lower() == "n" and not allow_n:
                error("n is not valid for this question. Enter a valid value.")
                continue
            return value
        error("This value cannot be empty. Try again.")


def ask_yes_no(prompt: str, default: bool) -> bool:
    default_text = "y" if default else "n"
    while True:
        value = ask_raw(prompt)
        if value == "0":
            raise Back()
        if not value:
            return default
        lowered = value.lower()
        if lowered in {"y", "yes"}:
            return True
        if lowered in {"n", "no"}:
            return False
        error(f"Enter only y or n. Default on Enter: {default_text}")


def ask_positive_int_or_n(prompt: str, allow_n: bool = True, allow_zero_word: bool = False) -> int | str:
    while True:
        value = ask_required(prompt, allow_n=allow_n)
        lowered = value.lower()
        if lowered == "n" and allow_n:
            return "n"
        if allow_zero_word and value == "00":
            return 0
        if not re.fullmatch(r"\d+", value):
            error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        return number


def ask_selection(
    prompt: str,
    max_count: int,
    default: list[int],
    allow_none: bool = False,
) -> list[int] | str:
    while True:
        value = ask_raw(prompt)
        lowered = value.lower()
        if not value:
            return default
        if lowered in {"b", "back"}:
            raise Back()
        if lowered == "n" or lowered == "all":
            return "all"
        if allow_none and lowered in {"none", "no", "clear", "delete"}:
            return []

        pieces = [piece.strip() for piece in value.split(",") if piece.strip()]
        if not pieces:
            error("Invalid selection.")
            continue
        if any(not re.fullmatch(r"\d+", piece) for piece in pieces):
            error("Enter numbers separated by commas, for example: 0,1,2")
            continue

        numbers = [int(piece) for piece in pieces]
        bad = [number for number in numbers if number < 0 or number >= max_count]
        if bad:
            error(f"Invalid number(s): {bad}. Allowed range: 0 to {max_count - 1}")
            continue
        return sorted(set(numbers))


def normalize_format(value: str, input_ext: str) -> str:
    lowered = value.lower().strip().lstrip(".")
    if lowered == "n":
        return input_ext.lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_+-]*", lowered):
        raise ValueError("Format must be like mp4, mkv, or mp3.")
    return lowered


def parse_resolution(value: str) -> dict[str, Any] | str:
    lowered = value.lower().strip()
    if lowered == "n":
        return "n"
    if lowered in RESOLUTION_PRESETS:
        width, height = RESOLUTION_PRESETS[lowered]
        return {"mode": "preset", "label": lowered, "width": width, "height": height}
    if re.fullmatch(r"\d{3,4}", lowered) and f"{lowered}p" in RESOLUTION_PRESETS:
        label = f"{lowered}p"
        width, height = RESOLUTION_PRESETS[label]
        return {"mode": "preset", "label": label, "width": width, "height": height}
    width_match = re.fullmatch(r"(?:w|width=)(\d{2,5})", lowered) or re.fullmatch(r"(\d{2,5})w", lowered)
    if width_match:
        width = int(width_match.group(1))
        if width == 0:
            raise Back()
        return {"mode": "width", "width": width, "label": f"{width}w"}
    height_match = re.fullmatch(r"(?:h|height=)(\d{2,5})", lowered) or re.fullmatch(r"(\d{2,5})h", lowered)
    if height_match:
        height = int(height_match.group(1))
        if height == 0:
            raise Back()
        return {"mode": "height", "height": height, "label": f"{height}h"}
    stretch_match = re.fullmatch(r"(?:stretch|exact):\s*(\d{2,5})\s*[xX]\s*(\d{2,5})", lowered)
    if stretch_match:
        width = int(stretch_match.group(1))
        height = int(stretch_match.group(2))
        if width == 0 or height == 0:
            raise Back()
        return {"mode": "exact_stretch", "width": width, "height": height, "label": f"stretch:{width}x{height}"}
    match = re.fullmatch(r"(\d{2,5})\s*[xX]\s*(\d{2,5})", lowered)
    if match:
        width = int(match.group(1))
        height = int(match.group(2))
        if width == 0 or height == 0:
            raise Back()
        return {"mode": "box", "width": width, "height": height, "label": f"{width}x{height}"}
    raise ValueError("Invalid resolution. Example: 480p, 480, w720, h480, 1280x720, or stretch:1280x720")


def first_video_size(answers: dict[str, Any]) -> tuple[int, int]:
    stream = answers["video_streams"][0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("Could not detect source video dimensions.")
    return width, height


def preview_size(source_width: int, source_height: int) -> tuple[int, int]:
    scale = min(1280 / source_width, 720 / source_height, 1.0)
    return max(1, round(source_width * scale)), max(1, round(source_height * scale))


def extract_crop_preview_frame(
    answers: dict[str, Any],
    temp_dir: Path,
    width: int,
    height: int,
    timestamp: float | None = None,
) -> Path:
    input_path: Path = answers["input_path"]
    safe_timestamp = max(0.0, timestamp or 0.0)
    output_path = temp_dir / f"crop_preview_{round(safe_timestamp * 1000)}_{width}x{height}.png"

    def run_extract(use_gpu: bool) -> None:
        args = [
            answers["ffmpeg"],
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        if use_gpu:
            args.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])
        if safe_timestamp > 0:
            args.extend(["-ss", f"{safe_timestamp:.3f}", "-noaccurate_seek"])
        video_filter = f"scale={width}:{height}:flags=fast_bilinear"
        if use_gpu:
            video_filter = f"scale_cuda={width}:{height}:format=nv12,hwdownload,format=nv12,format=rgb24"
        args.extend(
            [
            "-i",
            str(input_path),
            "-an",
            "-sn",
            "-dn",
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            video_filter,
            str(output_path),
            ]
        )
        subprocess.run(
            args,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    if answers.get("use_gpu") and not answers.get("_crop_preview_gpu_failed"):
        try:
            run_extract(True)
            return output_path
        except subprocess.CalledProcessError:
            if not answers.get("_crop_preview_gpu_failed"):
                note("GPU crop preview refresh failed once; CPU preview refresh will be used instead.")
                answers["_crop_preview_gpu_failed"] = True

    run_extract(False)
    return output_path


def choose_crop_graphically(answers: dict[str, Any]) -> tuple[int, int, int, int] | None:
    """Open the Crop Editor GUI.

    Prefers the PySide6 implementation in ffmwiz_gui.py and falls back
    to the legacy Tk preview when PySide6 is not installed."""
    if answers.get("video_streams"):
        try:
            source_w, source_h = first_video_size(answers)
        except Exception:
            source_w, source_h = 1920, 1080
    else:
        source_w, source_h = 1920, 1080
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "crop",
        "input_path": str(answers["input_path"]),
        "fps": float(get_video_fps(answers)) if "get_video_fps" in globals() else 25.0,
        "duration": float(duration),
        "source_w": int(source_w),
        "source_h": int(source_h),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = _launch_qt_gui(request)
    if reply is not None:
        if reply.get("status") == "ok":
            margins = reply.get("margins") or [0, 0, 0, 0]
            try:
                t, l, r, b = (int(x) for x in margins)
                return t, l, r, b
            except Exception:
                return None
        if reply.get("status") == "error":
            answers["_last_gui_error"] = "crop"
            error("Crop GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
            return None
        return None
    note(
        "Falling back to the legacy Tk crop preview. To enable the new GUI later, "
        "run:  py -3 -m pip install -r requirements.txt  (or restart FFmWiz with "
        "FFMWIZ_AUTO_INSTALL=1)."
    )
    return _choose_crop_graphically_tk(answers)


def _choose_crop_graphically_tk(answers: dict[str, Any]) -> tuple[int, int, int, int] | None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:
        error(f"Tkinter is not available, so graphical crop editor cannot be opened: {exc}")
        return None

    try:
        source_width, source_height = first_video_size(answers)
        frame_width, frame_height = preview_size(source_width, source_height)
        duration = stream_duration_seconds(answers.get("video_streams", [{}])[0], answers.get("format"))
        if duration is None:
            duration = stream_duration_seconds({}, answers.get("format"))
        timeline_duration = max(1.0, duration or 60.0)
        initial_timestamp = min(30.0, max(0.0, timeline_duration * 0.25))
        ffplay = shutil.which("ffplay")
        with tempfile.TemporaryDirectory(prefix="ffmwizard_crop_") as temp_name:
            temp_dir = Path(temp_name)

            result: dict[str, tuple[int, int, int, int] | None] = {"margins": None}
            palette = _UIPalette
            root = tk.Tk()
            root.title("FFmWiz Crop Editor")
            root.configure(bg=palette.BG)
            _apply_tk_window_icon(root)
            _apply_app_ttk_theme(root)
            try:
                style = ttk.Style(root)
                # Combobox styling specific to this GUI (zoom dropdown).
                style.configure(
                    "TCombobox",
                    fieldbackground=palette.SURFACE,
                    background=palette.SURFACE,
                    foreground=palette.TEXT,
                    arrowcolor=palette.ACCENT_YELLOW,
                )
                style.map(
                    "TCombobox",
                    fieldbackground=[("readonly", palette.SURFACE)],
                    foreground=[("readonly", palette.TEXT)],
                )
            except Exception:
                pass
            _apply_dark_title_bar(root)
            load_icon_image = _make_icon_loader(root)

            pad = 32
            image_x = pad
            image_y = pad
            min_size = 24
            handle_radius = 8
            handle_hit_radius = 26
            edge_hit_radius = 14
            min_zoom = 25
            max_zoom = 1000

            state: dict[str, Any] = {
                "left": 0,
                "top": 0,
                "right": 0,
                "bottom": 0,
                "drag": "",
                "pan": False,
                "timestamp": initial_timestamp,
                "zoom_percent": 100,
                "photo": None,
                "photo_key": None,
                "playing": False,
                "audio_proc": None,
                "volume": 50,
                "mute": False,
                # Active tool: "hand" pans the image, "zoom" zooms on click/drag.
                # Alt held while dragging in zoom mode inverts the zoom direction.
                "tool": "hand",
                "zoom_drag_y": None,
            }

            viewport_width = min(frame_width + pad * 2, 1280)
            viewport_height = min(frame_height + pad * 2, 760)
            root.minsize(min(viewport_width + 70, 1350), min(viewport_height + 150, 930))

            shell = ttk.Frame(root, padding=(14, 14, 14, 12))
            shell.pack(fill="both", expand=True)
            shell.columnconfigure(0, weight=1)
            shell.rowconfigure(0, weight=1)
            canvas = tk.Canvas(
                shell,
                width=viewport_width,
                height=viewport_height,
                bg="#11151d",
                highlightthickness=0,
            )
            canvas.grid(row=0, column=0, sticky="nsew")
            y_scroll = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview, style="Dark.Vertical.TScrollbar")
            x_scroll = ttk.Scrollbar(shell, orient="horizontal", command=canvas.xview, style="Dark.Horizontal.TScrollbar")
            y_scroll.grid(row=0, column=1, sticky="ns")
            x_scroll.grid(row=1, column=0, sticky="ew")
            canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)

            info = ttk.Label(shell, text="", anchor="w")
            info.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 8))
            controls = ttk.Frame(shell)
            controls.grid(row=3, column=0, columnspan=2, sticky="ew")
            media_controls = ttk.Frame(shell)
            media_controls.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            zoom_var = tk.StringVar(value="100")
            time_var = tk.DoubleVar(value=initial_timestamp)
            time_label = ttk.Label(media_controls, text="")
            play_button: Any | None = None
            volume_var = tk.IntVar(value=50)
            mute_var = tk.BooleanVar(value=False)
            frame_cache: dict[tuple[int, int, int], Path] = {}
            icon_cache: dict[str, Any] = {}
            zoom_presets = ["25", "33", "50", "67", "75", "90", "100", "110", "125", "150", "175", "200", "250", "300", "400", "500", "600", "800", "1000"]
            speaker_button: Any | None = None
            volume_slider: Any | None = None

            def load_icon(name: str) -> Any | None:
                if name in icon_cache:
                    return icon_cache[name]
                path = asset_path(ICON_DIR_NAME, f"{name}.png")
                if not path.exists():
                    icon_cache[name] = None
                    return None
                try:
                    icon_cache[name] = tk.PhotoImage(file=str(path))
                except tk.TclError:
                    icon_cache[name] = None
                return icon_cache[name]

            def draw_round_rect(target: Any, x1: int, y1: int, x2: int, y2: int, radius: int, fill: str, outline: str) -> None:
                radius = min(radius, max(1, (x2 - x1) // 2), max(1, (y2 - y1) // 2))
                centers = [
                    (x2 - radius, y1 + radius, -90, 0),
                    (x2 - radius, y2 - radius, 0, 90),
                    (x1 + radius, y2 - radius, 90, 180),
                    (x1 + radius, y1 + radius, 180, 270),
                ]
                points: list[float] = []
                for cx, cy, start, end in centers:
                    for angle in range(start, end + 1, 15):
                        radians = math.radians(angle)
                        points.extend([cx + math.cos(radians) * radius, cy + math.sin(radians) * radius])
                target.create_polygon(points, fill=fill, outline=outline, width=1, smooth=True)

            def make_round_button(parent: Any, text: str, command: Callable[[], None], width: int = 92, height: int = 32) -> Any:
                button = tk.Canvas(parent, width=width, height=height, bg="#0b0f17", highlightthickness=0, bd=0, relief="flat")
                label = {"text": text}

                def draw(active: bool = False) -> None:
                    button.delete("all")
                    draw_round_rect(button, 2, 2, width - 3, height - 3, 12, "#263550" if active else "#1b2433", "#5b6f91")
                    button.create_text(width // 2, height // 2, text=label["text"], fill="#f5f7fb", font=("Segoe UI", 9))

                def set_text(new_text: str) -> None:
                    label["text"] = new_text
                    draw(False)

                def on_press(_event: Any) -> None:
                    draw(True)

                def on_release(_event: Any) -> None:
                    draw(False)
                    command()

                button.bind("<ButtonPress-1>", on_press)
                button.bind("<ButtonRelease-1>", on_release)
                button.bind("<Enter>", lambda _event: button.configure(cursor="hand2"))
                button.bind("<Leave>", lambda _event: draw(False))
                button.set_text = set_text  # type: ignore[attr-defined]
                draw(False)
                return button

            def make_icon_button(parent: Any, kind: str, command: Callable[[], None], width: int = 46, height: int = 36) -> Any:
                button = tk.Canvas(parent, width=width, height=height, bg="#0b0f17", highlightthickness=0, bd=0, relief="flat")
                button.pack_propagate(False)

                def draw_icon(active: bool = False) -> None:
                    button.delete("all")
                    bg = "#263550" if active else "#1b2433"
                    fg = "#f8fbff"
                    accent = "#f5d66a"
                    muted_line = "#46556d"
                    draw_round_rect(button, 2, 2, width - 3, height - 3, 12, bg, "#5b6f91")
                    icon_name = kind
                    if kind == "speaker":
                        volume = int(volume_var.get())
                        level = 0 if mute_var.get() or volume <= 0 else 1 if volume < 34 else 2 if volume < 67 else 3
                        icon_name = f"volume_{level}"
                    icon = load_icon(icon_name)
                    if icon is not None:
                        button.create_image(width // 2, height // 2, image=icon)
                    elif kind in {"zoom_in", "zoom_out"}:
                        button.create_oval(9, 6, 23, 20, outline=accent, width=2)
                        button.create_line(21, 19, 30, 26, fill=accent, width=2)
                        button.create_line(13, 13, 19, 13, fill=fg, width=2)
                        if kind == "zoom_in":
                            button.create_line(16, 10, 16, 16, fill=fg, width=2)
                    elif kind == "speaker":
                        volume = int(volume_var.get())
                        level = 0 if mute_var.get() or volume <= 0 else 1 if volume <= 33 else 2 if volume <= 66 else 3
                        button.create_polygon(7, 13, 13, 13, 20, 7, 20, 23, 13, 17, 7, 17, fill=accent, outline="")
                        if mute_var.get():
                            button.create_line(25, 10, 33, 20, fill="#ff6f6f", width=2)
                            button.create_line(33, 10, 25, 20, fill="#ff6f6f", width=2)
                        else:
                            button.create_arc(21, 11, 28, 19, start=-35, extent=70, style="arc", outline=fg if level >= 1 else muted_line, width=2)
                            button.create_arc(19, 8, 33, 22, start=-35, extent=70, style="arc", outline=fg if level >= 2 else muted_line, width=2)
                            button.create_arc(17, 5, 38, 25, start=-35, extent=70, style="arc", outline=fg if level >= 3 else muted_line, width=2)

                def on_press(_event: Any) -> None:
                    draw_icon(True)

                def on_release(_event: Any) -> None:
                    draw_icon(False)
                    command()

                button.bind("<ButtonPress-1>", on_press)
                button.bind("<ButtonRelease-1>", on_release)
                button.bind("<Enter>", lambda _event: button.configure(cursor="hand2"))
                button.bind("<Leave>", lambda _event: draw_icon(False))
                draw_icon(False)
                button.redraw_icon = draw_icon  # type: ignore[attr-defined]
                return button

            def clamp(value: float, low: float, high: float) -> float:
                return max(low, min(high, value))

            def display_width() -> int:
                return max(1, round(frame_width * int(state["zoom_percent"]) / 100))

            def display_height() -> int:
                return max(1, round(frame_height * int(state["zoom_percent"]) / 100))

            def image_bounds() -> tuple[int, int, int, int]:
                return image_x, image_y, image_x + display_width(), image_y + display_height()

            def format_time(seconds: float) -> str:
                total = max(0, int(round(seconds)))
                minutes, secs = divmod(total, 60)
                hours, minutes = divmod(minutes, 60)
                if hours:
                    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
                return f"{minutes:02d}:{secs:02d}"

            # --- Async preview scheduler -------------------------------------
            # Background-extract frames on a worker thread with debouncing and
            # caching. The cache is keyed by (timestamp_ms, width, height) so
            # zoom changes invalidate naturally.

            def _crop_extract(timestamp: float, w: int, h: int) -> Path:
                return extract_crop_preview_frame(answers, temp_dir, w, h, timestamp)

            def _crop_on_ready(path: Path) -> None:
                try:
                    state["photo"] = tk.PhotoImage(file=str(path))
                except Exception:
                    return
                state["photo_key"] = (
                    int(round(float(state["timestamp"]) * 1000)),
                    display_width(),
                    display_height(),
                )
                redraw(force_image_request=False)

            crop_scheduler = _PreviewScheduler(
                root,
                extract_fn=_crop_extract,
                on_ready=_crop_on_ready,
                debounce_ms=70,
            )

            def render_photo(force: bool = True) -> None:
                """Request the current frame from the scheduler.

                If a cached path is available, set state['photo'] immediately
                so the next canvas draw shows the correct frame. Otherwise the
                scheduler will deliver it asynchronously and trigger a redraw.
                """
                key = (
                    int(round(float(state["timestamp"]) * 1000)),
                    display_width(),
                    display_height(),
                )
                if state.get("photo_key") == key and state.get("photo") is not None:
                    return
                cached = crop_scheduler.request(float(state["timestamp"]), key[1], key[2])
                if cached is not None:
                    try:
                        state["photo"] = tk.PhotoImage(file=str(cached))
                        state["photo_key"] = key
                    except Exception:
                        state["photo_key"] = None
                # Else: keep showing the previous frame; the worker will
                # deliver the new one and trigger a redraw via _crop_on_ready.

            def min_source_width() -> int:
                return max(1, round(source_width * min_size / display_width()))

            def min_source_height() -> int:
                return max(1, round(source_height * min_size / display_height()))

            def clamp_margins() -> None:
                state["left"] = int(clamp(state["left"], 0, max(0, source_width - state["right"] - min_source_width())))
                state["right"] = int(clamp(state["right"], 0, max(0, source_width - state["left"] - min_source_width())))
                state["top"] = int(clamp(state["top"], 0, max(0, source_height - state["bottom"] - min_source_height())))
                state["bottom"] = int(clamp(state["bottom"], 0, max(0, source_height - state["top"] - min_source_height())))

            def current_margins() -> tuple[int, int, int, int]:
                clamp_margins()
                return int(state["top"]), int(state["left"]), int(state["right"]), int(state["bottom"])

            def crop_rect() -> tuple[int, int, int, int]:
                _, _, image_right, image_bottom = image_bounds()
                left = image_x + round(state["left"] * display_width() / source_width)
                top = image_y + round(state["top"] * display_height() / source_height)
                right = image_right - round(state["right"] * display_width() / source_width)
                bottom = image_bottom - round(state["bottom"] * display_height() / source_height)
                return left, top, right, bottom

            def handle_points() -> dict[str, tuple[int, int]]:
                left, top, right, bottom = crop_rect()
                mid_x = round((left + right) / 2)
                mid_y = round((top + bottom) / 2)
                return {
                    "nw": (left, top),
                    "n": (mid_x, top),
                    "ne": (right, top),
                    "e": (right, mid_y),
                    "se": (right, bottom),
                    "s": (mid_x, bottom),
                    "sw": (left, bottom),
                    "w": (left, mid_y),
                }

            def redraw(force_image_request: bool = True) -> None:
                clamp_margins()
                if force_image_request:
                    try:
                        render_photo()
                    except Exception as exc:
                        error(f"Could not refresh crop preview frame: {exc}")
                canvas.delete("all")

                image_left, image_top, image_right, image_bottom = image_bounds()
                if state.get("photo") is not None:
                    canvas.create_image(image_left, image_top, image=state["photo"], anchor="nw")
                else:
                    # Show a placeholder until the worker delivers the first frame.
                    canvas.create_rectangle(
                        image_left, image_top, image_right, image_bottom,
                        fill="#11151d", outline="#2e3a4f", width=1,
                    )
                    canvas.create_text(
                        (image_left + image_right) // 2,
                        (image_top + image_bottom) // 2,
                        text="(loading preview frame...)",
                        fill="#7c8aa6",
                    )
                left, top, right, bottom = crop_rect()
                canvas.create_rectangle(image_left, image_top, image_right, top, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(image_left, bottom, image_right, image_bottom, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(image_left, top, left, bottom, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(right, top, image_right, bottom, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(left, top, right, bottom, outline="#ffcc33", width=2)

                third_x = (right - left) / 3
                third_y = (bottom - top) / 3
                for pos in (left + third_x, left + third_x * 2):
                    canvas.create_line(pos, top, pos, bottom, fill="#ffcc33", dash=(4, 5), width=1)
                for pos in (top + third_y, top + third_y * 2):
                    canvas.create_line(left, pos, right, pos, fill="#ffcc33", dash=(4, 5), width=1)

                for name, (x_pos, y_pos) in handle_points().items():
                    fill = "#f8fbff" if len(name) == 1 else "#ffcc33"
                    canvas.create_rectangle(
                        x_pos - handle_radius,
                        y_pos - handle_radius,
                        x_pos + handle_radius,
                        y_pos + handle_radius,
                        fill=fill,
                        outline="#11151d",
                        width=1,
                    )
                top_m, left_m, right_m, bottom_m = current_margins()
                crop_width = source_width - left_m - right_m
                crop_height = source_height - top_m - bottom_m
                info.configure(
                    text=(
                        f"Crop margins: top={top_m}, left={left_m}, right={right_m}, bottom={bottom_m} "
                        f"| output crop box: {crop_width}x{crop_height} | time {format_time(float(state['timestamp']))}"
                    )
                )
                canvas.configure(scrollregion=(0, 0, image_right + pad, image_bottom + pad))
                zoom_var.set(str(int(state["zoom_percent"])))
                time_var.set(float(state["timestamp"]))
                time_label.configure(text=f"{format_time(float(state['timestamp']))} / {format_time(timeline_duration)}")

            def hit_handle(x_pos: float, y_pos: float) -> str:
                left, top, right, bottom = crop_rect()
                in_x = left - edge_hit_radius <= x_pos <= right + edge_hit_radius
                in_y = top - edge_hit_radius <= y_pos <= bottom + edge_hit_radius

                corner_zones = {
                    "nw": (left, top),
                    "ne": (right, top),
                    "se": (right, bottom),
                    "sw": (left, bottom),
                }
                for name, (corner_x, corner_y) in corner_zones.items():
                    if abs(x_pos - corner_x) <= handle_hit_radius and abs(y_pos - corner_y) <= handle_hit_radius:
                        return name

                for name, (handle_x, handle_y) in handle_points().items():
                    if abs(x_pos - handle_x) <= handle_hit_radius and abs(y_pos - handle_y) <= handle_hit_radius:
                        return name

                if in_x and abs(y_pos - top) <= edge_hit_radius:
                    return "n"
                if in_x and abs(y_pos - bottom) <= edge_hit_radius:
                    return "s"
                if in_y and abs(x_pos - left) <= edge_hit_radius:
                    return "w"
                if in_y and abs(x_pos - right) <= edge_hit_radius:
                    return "e"
                return ""

            def cursor_for_handle(handle: str) -> str:
                if handle in {"e", "w"}:
                    return "sb_h_double_arrow"
                if handle in {"n", "s"}:
                    return "sb_v_double_arrow"
                if handle in {"nw", "se"}:
                    return "size_nw_se"
                if handle in {"ne", "sw"}:
                    return "size_ne_sw"
                return ""

            def point_in_image(x_pos: float, y_pos: float) -> bool:
                image_left, image_top, image_right, image_bottom = image_bounds()
                return image_left <= x_pos <= image_right and image_top <= y_pos <= image_bottom

            def set_canvas_cursor(cursor: str) -> None:
                try:
                    if cursor == "open_hand":
                        cursor_file = asset_path(CURSOR_DIR_NAME, "open_hand.xbm")
                        if cursor_file.exists():
                            canvas.configure(cursor=f"@{cursor_file}")
                            return
                        canvas.configure(cursor="hand1")
                        return
                    canvas.configure(cursor=cursor)
                except tk.TclError:
                    fallback = "fleur" if cursor == "open_hand" else "crosshair" if cursor else ""
                    canvas.configure(cursor=fallback)

            def _alt_held(event: Any) -> bool:
                """Return True if any Alt modifier is held in the event.state mask."""
                if event is None:
                    return False
                mask = getattr(event, "state", 0) or 0
                # Windows: Alt = 0x20000. Linux/X11: Mod1 = 0x0008.
                return bool(mask & 0x20000) or bool(mask & 0x0008)

            def update_cursor(event: Any) -> None:
                if state["drag"]:
                    return
                x_pos = canvas.canvasx(event.x)
                y_pos = canvas.canvasy(event.y)
                handle = hit_handle(x_pos, y_pos)
                if handle:
                    set_canvas_cursor(cursor_for_handle(handle))
                elif point_in_image(x_pos, y_pos):
                    if state["tool"] == "zoom":
                        # Use the universally-supported "crosshair" cursor
                        # for Zoom Tool. The Tk "icon" cursor used previously
                        # appeared as a black square on some Windows builds.
                        set_canvas_cursor("crosshair")
                    else:
                        set_canvas_cursor("open_hand")
                else:
                    set_canvas_cursor("")

            def apply_zoom_centered_on(x_pos: float, y_pos: float, factor: float) -> None:
                """Zoom the preview by 'factor' (>1 zoom in, <1 zoom out)
                keeping the canvas point (x_pos, y_pos) at the same screen
                location after the zoom."""
                if factor <= 0 or abs(factor - 1.0) < 1e-6:
                    return
                old_width = display_width()
                old_height = display_height()
                if old_width <= 0 or old_height <= 0:
                    return
                # Image-space coordinates of the focused canvas point.
                rel_x = (canvas.canvasx(x_pos) - image_x) / max(1, old_width)
                rel_y = (canvas.canvasy(y_pos) - image_y) / max(1, old_height)
                rel_x = max(0.0, min(1.0, rel_x))
                rel_y = max(0.0, min(1.0, rel_y))

                new_zoom = int(round(int(state["zoom_percent"]) * factor))
                new_zoom = int(clamp(new_zoom, min_zoom, max_zoom))
                if new_zoom == int(state["zoom_percent"]):
                    return
                state["zoom_percent"] = new_zoom
                state["photo_key"] = None  # force re-render at the new size
                redraw()
                # After redraw, re-center scroll so the focused image-relative
                # point lands under the original mouse position.
                root.update_idletasks()
                new_width = display_width()
                new_height = display_height()
                target_canvas_x = image_x + rel_x * new_width
                target_canvas_y = image_y + rel_y * new_height
                desired_x = target_canvas_x - x_pos
                desired_y = target_canvas_y - y_pos
                scroll_w = max(1, new_width + pad * 2)
                scroll_h = max(1, new_height + pad * 2)
                canvas.xview_moveto(max(0.0, min(1.0, desired_x / scroll_w)))
                canvas.yview_moveto(max(0.0, min(1.0, desired_y / scroll_h)))

            def begin_drag(event: Any) -> None:
                x_pos = canvas.canvasx(event.x)
                y_pos = canvas.canvasy(event.y)
                state["drag"] = hit_handle(x_pos, y_pos)
                if state["drag"]:
                    set_canvas_cursor(cursor_for_handle(state["drag"]))
                    canvas.focus_set()
                    return

                if point_in_image(x_pos, y_pos):
                    if state["tool"] == "zoom":
                        # Photoshop-style: clicking zooms in (or out with Alt).
                        # Hold + drag tracks vertical motion for finer control.
                        state["zoom_drag_y"] = event.y
                        state["drag"] = "_zoom"
                        # Single-click zoom step:
                        factor = 1.0 / 1.25 if _alt_held(event) else 1.25
                        apply_zoom_centered_on(event.x, event.y, factor)
                        update_cursor(event)
                    else:
                        # Hand tool: pan.
                        state["pan"] = True
                        canvas.scan_mark(event.x, event.y)
                        set_canvas_cursor("open_hand")
                canvas.focus_set()

            def drag(event: Any) -> None:
                if state.get("drag") == "_zoom" and state.get("zoom_drag_y") is not None:
                    dy = event.y - int(state["zoom_drag_y"])
                    if abs(dy) >= 6:
                        # Up = zoom in, down = zoom out. Alt inverts.
                        zoom_in_dir = dy < 0
                        if _alt_held(event):
                            zoom_in_dir = not zoom_in_dir
                        factor = 1.07 if zoom_in_dir else (1.0 / 1.07)
                        apply_zoom_centered_on(event.x, event.y, factor)
                        state["zoom_drag_y"] = event.y
                    return
                if state["pan"]:
                    canvas.scan_dragto(event.x, event.y, gain=1)
                    return
                mode = state["drag"]
                if not mode:
                    return
                image_left, image_top, image_right, image_bottom = image_bounds()
                left, top, right, bottom = crop_rect()
                x_pos = clamp(canvas.canvasx(event.x), image_left, image_right)
                y_pos = clamp(canvas.canvasy(event.y), image_top, image_bottom)
                if "w" in mode:
                    new_left = clamp(x_pos, image_left, right - min_size)
                    state["left"] = round((new_left - image_left) * source_width / display_width())
                if "e" in mode:
                    new_right = clamp(x_pos, left + min_size, image_right)
                    state["right"] = round((image_right - new_right) * source_width / display_width())
                if "n" in mode:
                    new_top = clamp(y_pos, image_top, bottom - min_size)
                    state["top"] = round((new_top - image_top) * source_height / display_height())
                if "s" in mode:
                    new_bottom = clamp(y_pos, top + min_size, image_bottom)
                    state["bottom"] = round((image_bottom - new_bottom) * source_height / display_height())
                redraw(force_image_request=False)

            def end_drag(_event: Any) -> None:
                state["drag"] = ""
                state["pan"] = False
                state["zoom_drag_y"] = None

            def set_tool_hand(_event: Any = None) -> None:
                state["tool"] = "hand"
                refresh_tool_label()

            def set_tool_zoom(_event: Any = None) -> None:
                state["tool"] = "zoom"
                refresh_tool_label()

            def refresh_tool_label() -> None:
                # The two tool buttons exist as attributes set later; update
                # their visible labels so the active tool is obvious.
                try:
                    hand_button.configure(
                        text=("Hand Tool* (H)" if state["tool"] == "hand" else "Hand Tool (H)")
                    )
                    zoom_button.configure(
                        text=("Zoom Tool* (Z)" if state["tool"] == "zoom" else "Zoom Tool (Z)")
                    )
                except Exception:
                    pass

            def reset_crop() -> None:
                state["left"] = 0
                state["top"] = 0
                state["right"] = 0
                state["bottom"] = 0
                redraw()

            def set_zoom_percent(value: int | str) -> None:
                try:
                    number = int(str(value).strip().rstrip("%"))
                except ValueError:
                    error("Zoom must be an integer percent, for example 150.")
                    return
                state["zoom_percent"] = int(clamp(number, min_zoom, max_zoom))
                state["photo_key"] = None
                redraw()

            def zoom_in() -> None:
                set_zoom_percent(int(state["zoom_percent"]) + 25)

            def zoom_out() -> None:
                set_zoom_percent(int(state["zoom_percent"]) - 25)

            def reset_zoom() -> None:
                set_zoom_percent(100)

            def set_time(value: float, restart_audio: bool = True) -> None:
                state["timestamp"] = clamp(float(value), 0.0, timeline_duration)
                state["photo_key"] = None
                redraw()
                if restart_audio and state["playing"]:
                    start_audio()

            def step_time(delta: float) -> None:
                set_time(float(state["timestamp"]) + delta)

            def stop_audio() -> None:
                process = state.get("audio_proc")
                state["audio_proc"] = None
                if process and process.poll() is None:
                    try:
                        process.terminate()
                        process.wait(timeout=0.8)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass

            def start_audio() -> None:
                stop_audio()
                if not ffplay or state["mute"] or int(state["volume"]) <= 0:
                    return
                args = [
                    ffplay,
                    "-nodisp",
                    "-autoexit",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{float(state['timestamp']):.3f}",
                    "-volume",
                    str(int(state["volume"])),
                    str(answers["input_path"]),
                ]
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                try:
                    state["audio_proc"] = subprocess.Popen(
                        args,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creation_flags,
                    )
                except Exception as exc:
                    error(f"Could not start ffplay audio preview: {exc}")

            def playback_tick() -> None:
                if not state["playing"]:
                    return
                next_time = float(state["timestamp"]) + 1.0
                if next_time >= timeline_duration:
                    state["timestamp"] = timeline_duration
                    state["playing"] = False
                    if play_button is not None:
                        play_button.set_text("▶ Play (Space)")  # type: ignore[attr-defined]
                    stop_audio()
                    redraw()
                    return
                state["timestamp"] = next_time
                state["photo_key"] = None
                redraw()
                root.after(1000, playback_tick)

            def toggle_playback() -> None:
                # Bug fix: if playback reached the end and the user presses
                # Play again, rewind to the start so playback continues
                # normally instead of ending after ~1 second.
                if not state["playing"] and float(state["timestamp"]) >= timeline_duration - 0.5:
                    state["timestamp"] = 0.0
                    state["photo_key"] = None
                    redraw()
                state["playing"] = not state["playing"]
                if play_button is not None:
                    play_button.set_text("⏸ Pause (Space)" if state["playing"] else "▶ Play (Space)")  # type: ignore[attr-defined]
                if state["playing"]:
                    start_audio()
                    root.after(1000, playback_tick)
                else:
                    stop_audio()

            def update_audio_settings(_event: Any | None = None, restart: bool = False) -> None:
                state["volume"] = int(volume_var.get())
                state["mute"] = bool(mute_var.get())
                if speaker_button is not None:
                    speaker_button.redraw_icon(False)  # type: ignore[attr-defined]
                draw_volume_slider()
                if state["playing"] and restart:
                    start_audio()

            def toggle_mute() -> None:
                mute_var.set(not mute_var.get())
                update_audio_settings(restart=True)
                if speaker_button is not None:
                    speaker_button.redraw_icon(False)  # type: ignore[attr-defined]

            def slider_value_from_click(widget: Any, event: Any, low: float, high: float) -> float:
                width = max(1, widget.winfo_width())
                ratio = clamp(event.x / width, 0.0, 1.0)
                return low + (high - low) * ratio

            def seek_time_from_click(event: Any) -> None:
                set_time(slider_value_from_click(time_slider, event, 0.0, timeline_duration))

            def set_volume_from_click(event: Any) -> None:
                set_volume_from_x(event.x, restart=True)

            def draw_volume_slider(active: bool = False) -> None:
                if volume_slider is None:
                    return
                width = int(volume_slider["width"])
                height = int(volume_slider["height"])
                left = 9
                right = width - 9
                center = height // 2
                volume_slider.delete("all")
                draw_round_rect(volume_slider, left, center - 4, right, center + 4, 4, "#101827", "#3f5576")
                fill_right = left + round((right - left) * int(volume_var.get()) / 100)
                if fill_right > left:
                    draw_round_rect(volume_slider, left, center - 4, fill_right, center + 4, 4, "#f5d66a", "#f5d66a")
                thumb_x = max(left, min(right, fill_right))
                thumb_fill = "#ffffff" if active else "#dfeaff"
                volume_slider.create_oval(thumb_x - 7, center - 7, thumb_x + 7, center + 7, fill=thumb_fill, outline="#6f8dc1", width=2)

            def set_volume_value(value: float, restart: bool = False) -> None:
                volume_var.set(int(clamp(round(value), 0, 100)))
                update_audio_settings(restart=restart)

            def set_volume_from_x(x_pos: float, restart: bool = False) -> None:
                if volume_slider is None:
                    return
                width = int(volume_slider["width"])
                left = 9
                right = width - 9
                ratio = clamp((x_pos - left) / max(1, right - left), 0.0, 1.0)
                set_volume_value(ratio * 100, restart=restart)

            def drag_volume(event: Any) -> str:
                set_volume_from_x(event.x, restart=False)
                return "break"

            def release_volume(event: Any) -> str:
                set_volume_from_x(event.x, restart=True)
                return "break"

            def volume_wheel(event: Any) -> str:
                delta = 3 if event.delta > 0 else -3
                set_volume_value(int(volume_var.get()) + delta, restart=True)
                return "break"

            def mouse_wheel(event: Any) -> str:
                step = -1 if event.delta > 0 else 1
                if event.state & 0x0001:
                    canvas.xview_scroll(step, "units")
                else:
                    canvas.yview_scroll(step, "units")
                return "break"

            def x_scroll_wheel(event: Any) -> str:
                canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"

            def y_scroll_wheel(event: Any) -> str:
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"

            def time_slider_wheel(event: Any) -> str:
                step_time(-5 if event.delta > 0 else 5)
                return "break"

            def ctrl_mouse_wheel(event: Any) -> str:
                if event.delta > 0:
                    zoom_in()
                else:
                    zoom_out()
                return "break"

            def pan_with_key(event: Any) -> str:
                moves = {
                    "Left": (-1, 0),
                    "Right": (1, 0),
                    "Up": (0, -1),
                    "Down": (0, 1),
                }
                dx, dy = moves.get(event.keysym, (0, 0))
                if dx:
                    canvas.xview_scroll(dx, "units")
                if dy:
                    canvas.yview_scroll(dy, "units")
                return "break"

            def apply_crop() -> None:
                result["margins"] = current_margins()
                state["playing"] = False
                stop_audio()
                try:
                    crop_scheduler.cancel()
                except Exception:
                    pass
                root.destroy()

            def cancel_crop() -> None:
                result["margins"] = None
                state["playing"] = False
                stop_audio()
                try:
                    crop_scheduler.cancel()
                except Exception:
                    pass
                root.destroy()

            def is_editing_text(event: Any) -> bool:
                """True if the current keyboard focus is on a text-entry widget."""
                widget = getattr(event, "widget", None) if event is not None else None
                try:
                    cls = str(widget.winfo_class()) if widget is not None else ""
                except Exception:
                    cls = ""
                return cls in ("Entry", "TEntry", "Text", "Spinbox", "TCombobox", "TSpinbox")

            def space_toggle_playback(event: Any) -> str | None:
                if is_editing_text(event):
                    return None
                toggle_playback()
                return "break"

            canvas.bind("<ButtonPress-1>", begin_drag)
            canvas.bind("<B1-Motion>", drag)
            canvas.bind("<ButtonRelease-1>", end_drag)
            canvas.bind("<Motion>", update_cursor)
            canvas.bind("<Leave>", lambda _event: set_canvas_cursor(""))
            canvas.bind("<MouseWheel>", mouse_wheel)
            canvas.bind("<Control-MouseWheel>", ctrl_mouse_wheel)
            x_scroll.bind("<MouseWheel>", x_scroll_wheel)
            y_scroll.bind("<MouseWheel>", y_scroll_wheel)
            root.bind("<Control-plus>", lambda _event: zoom_in())
            root.bind("<Control-equal>", lambda _event: zoom_in())
            root.bind("<Control-minus>", lambda _event: zoom_out())
            for key_name in ("<Left>", "<Right>", "<Up>", "<Down>"):
                root.bind(key_name, pan_with_key)

            # Layout-independent shortcuts so the Crop GUI still responds
            # when the active keyboard language is Persian or another
            # non-Latin layout (these bindings match by Windows VK code).
            _bind_layout_independent_keys(root, [
                {"key": "space", "callback": lambda _e: space_toggle_playback(_e)},
                {"key": "h", "ctrl": False, "alt": False, "callback": set_tool_hand},
                {"key": "z", "ctrl": False, "alt": False, "callback": set_tool_zoom},
                {"key": "r", "ctrl": False, "callback": lambda _e: reset_crop()},
                {"key": "r", "ctrl": True, "callback": lambda _e: reset_crop()},
                {"key": "m", "ctrl": False, "callback": lambda _e: toggle_mute()},
                {"key": "0", "ctrl": True, "callback": lambda _e: reset_zoom()},
                {"key": "plus", "ctrl": True, "callback": lambda _e: zoom_in()},
                {"key": "minus", "ctrl": True, "callback": lambda _e: zoom_out()},
                {"key": "plus", "ctrl": False, "callback": lambda _e: zoom_in()},
                {"key": "minus", "ctrl": False, "callback": lambda _e: zoom_out()},
                {"key": "left", "shift": True, "callback": lambda _e: step_time(-10)},
                {"key": "right", "shift": True, "callback": lambda _e: step_time(10)},
                {"key": "return", "callback": lambda _e: apply_crop()},
                {"key": "escape", "callback": lambda _e: cancel_crop()},
            ], is_text_focus_fn=is_editing_text)

            root.protocol("WM_DELETE_WINDOW", cancel_crop)
            # Tool selector buttons. Hand is the default to preserve the
            # previous panning behavior; Zoom Tool is opt-in via Z.
            hand_button = make_round_button(controls, "Hand Tool* (H)", set_tool_hand, width=120)
            hand_button.pack(side="left")
            zoom_button = make_round_button(controls, "Zoom Tool (Z)", set_tool_zoom, width=120)
            zoom_button.pack(side="left", padx=(6, 12))
            make_icon_button(controls, "zoom_out", zoom_out).pack(side="left")
            ttk.Label(controls, text="Zoom %").pack(side="left", padx=(8, 4))
            zoom_box = tk.Frame(
                controls,
                bg="#101827",
                highlightthickness=1,
                highlightbackground="#3a4b65",
                highlightcolor="#f5d66a",
            )
            zoom_box.pack(side="left")
            zoom_entry = tk.Entry(
                zoom_box,
                textvariable=zoom_var,
                width=5,
                bg="#101827",
                fg="#f5f7fb",
                insertbackground="#f5d66a",
                relief="flat",
                highlightthickness=0,
                justify="center",
            )
            zoom_entry.pack(side="left", ipady=5)
            zoom_entry.bind("<Return>", lambda _event: set_zoom_percent(zoom_var.get()))
            zoom_arrow = tk.Canvas(zoom_box, width=22, height=28, bg="#101827", highlightthickness=0, bd=0, relief="flat")
            zoom_arrow.pack(side="left")
            zoom_arrow.create_polygon(7, 10, 15, 10, 11, 16, fill="#f5d66a", outline="")
            zoom_menu = tk.Menu(
                root,
                tearoff=False,
                bg="#101827",
                fg="#f5f7fb",
                activebackground="#263550",
                activeforeground="#ffffff",
                bd=1,
                relief="solid",
            )
            for preset in zoom_presets:
                zoom_menu.add_command(label=f"{preset}%", command=lambda value=preset: set_zoom_percent(value))

            def show_zoom_menu() -> None:
                zoom_menu.tk_popup(zoom_box.winfo_rootx(), zoom_box.winfo_rooty() + zoom_box.winfo_height())

            zoom_arrow.bind("<Button-1>", lambda _event: show_zoom_menu())
            zoom_arrow.bind("<Enter>", lambda _event: zoom_arrow.configure(cursor="hand2"))
            make_icon_button(controls, "zoom_in", zoom_in).pack(side="left", padx=(12, 0))
            make_round_button(controls, "Reset Zoom (Ctrl+0)", reset_zoom, width=146).pack(side="left", padx=(8, 0))
            make_round_button(controls, "Reset Crop (Ctrl+R)", reset_crop, width=154).pack(side="left", padx=(8, 0))
            make_round_button(controls, "Cancel (Esc)", cancel_crop, width=104).pack(side="right", padx=(8, 0))
            make_round_button(controls, "Apply (Enter)", apply_crop, width=110).pack(side="right")

            play_button = make_round_button(media_controls, "▶ Play (Space)", toggle_playback, width=120)
            play_button.pack(side="left")
            make_round_button(media_controls, "-10s (Shift+←)", lambda: step_time(-10), width=110).pack(side="left", padx=(8, 0))
            time_slider = ttk.Scale(
                media_controls,
                from_=0.0,
                to=timeline_duration,
                orient="horizontal",
                variable=time_var,
            )
            time_slider.pack(side="left", fill="x", expand=True, padx=8)
            time_slider.bind("<Button-1>", seek_time_from_click)
            time_slider.bind("<ButtonRelease-1>", lambda _event: set_time(time_var.get()))
            time_slider.bind("<MouseWheel>", time_slider_wheel)
            make_round_button(media_controls, "+10s (Shift+→)", lambda: step_time(10), width=110).pack(side="left")
            time_label.pack(side="left", padx=(8, 16))
            speaker_button = make_icon_button(media_controls, "speaker", toggle_mute, width=48, height=36)
            speaker_button.pack(side="left")
            # Home / End jump to start / end of the audio-preview timeline.
            ttk.Button(media_controls, text="⏮", width=3,
                       command=lambda: set_time(0.0)).pack(side="left", padx=(8, 0))
            ttk.Button(media_controls, text="⏭", width=3,
                       command=lambda: set_time(timeline_duration)).pack(side="left", padx=(4, 0))
            _bind_layout_independent_keys(root, [
                {"key": "home", "callback": lambda _e: set_time(0.0)},
                {"key": "end", "callback": lambda _e: set_time(timeline_duration)},
            ], is_text_focus_fn=is_editing_text)
            volume_slider = tk.Canvas(
                media_controls,
                width=142,
                height=30,
                bg="#0b0f17",
                highlightthickness=0,
                bd=0,
                relief="flat",
            )
            volume_slider.pack(side="left", padx=(8, 0))
            volume_slider.bind("<Button-1>", set_volume_from_click)
            volume_slider.bind("<B1-Motion>", drag_volume)
            volume_slider.bind("<ButtonRelease-1>", release_volume)
            volume_slider.bind("<MouseWheel>", volume_wheel)
            volume_slider.bind("<Enter>", lambda _event: volume_slider.configure(cursor="hand2") if volume_slider is not None else None)
            if not ffplay:
                speaker_button.unbind("<ButtonPress-1>")
                speaker_button.unbind("<ButtonRelease-1>")
                if volume_slider is not None:
                    volume_slider.unbind("<Button-1>")
                    volume_slider.unbind("<B1-Motion>")
                    volume_slider.unbind("<ButtonRelease-1>")
                    volume_slider.unbind("<MouseWheel>")
                ttk.Label(media_controls, text="Audio preview needs ffplay").pack(side="left", padx=(8, 0))
            draw_volume_slider()
            redraw()
            canvas.focus_set()
            root.mainloop()
            return result["margins"]
    except subprocess.CalledProcessError as exc:
        error(f"FFmpeg could not create a crop preview frame: {exc}")
    except Exception as exc:
        error(f"Graphical crop preview failed: {exc}")
    return None


def open_cut_gui(
    answers: dict[str, Any],
    fps: float,
    duration: float,
) -> list[tuple[float, float]] | None:
    """Open the Cut Editor GUI.

    Prefers the new PySide6 implementation in ffmwiz_gui.py (launched as
    a subprocess via JSON IPC). Falls back to the legacy Tk GUI when
    PySide6 is not installed so the workflow keeps working everywhere.
    """
    request = {
        "mode": "cut",
        "input_path": str(answers["input_path"]),
        "fps": float(fps),
        "duration": float(duration),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "ffprobe": answers.get("ffprobe") or shutil.which("ffprobe") or "ffprobe",
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = _launch_qt_gui(request)
    if reply is not None:
        if reply.get("status") == "ok":
            ranges = reply.get("keep_ranges") or []
            normalized: list[tuple[float, float]] = []
            for entry in ranges:
                try:
                    s, e = float(entry[0]), float(entry[1])
                except Exception:
                    continue
                if e > s:
                    normalized.append((s, e))
            return normalized
        if reply.get("status") == "error":
            answers["_last_gui_error"] = "cut"
            error("Cut GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
            return None
        return None  # canceled
    note(
        "Falling back to the legacy Tk cut editor. To enable the new GUI later, "
        "run:  py -3 -m pip install -r requirements.txt  (or restart FFmWiz with "
        "FFMWIZ_AUTO_INSTALL=1)."
    )
    return _open_legacy_cut_gui_tk(answers, fps, duration)


def open_video_speed_gui(answers: dict[str, Any]) -> dict[str, Any] | None:
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "video_speed",
        "input_path": str(answers["input_path"]),
        "duration": float(duration),
        "fps": float(get_video_fps(answers)),
        "has_audio": bool(answers.get("audio_streams")),
        "audio_count": len(answers.get("audio_streams") or []),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = _launch_qt_gui(request)
    if reply is None:
        error("Graphical video speed editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        try:
            return {
                "speed": clamp_speed_factor(reply.get("speed", DEFAULT_SPEED_FACTOR)),
                "reverse": bool(reply.get("reverse")),
                "include_audio": bool(reply.get("include_audio", True)),
            }
        except ValueError as exc:
            error(str(exc))
            return None
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "video_speed"
        error("Video speed GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


def open_unified_video_gui(answers: dict[str, Any]) -> dict[str, Any] | None:
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    try:
        source_w, source_h = first_video_size(answers)
    except Exception:
        source_w, source_h = 1920, 1080
    request = {
        "mode": "video_unified",
        "input_path": str(answers["input_path"]),
        "duration": float(duration),
        "fps": float(get_video_fps(answers)),
        "source_w": int(source_w),
        "source_h": int(source_h),
        "has_audio": bool(answers.get("audio_streams")),
        "audio_count": len(answers.get("audio_streams") or []),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
        "start_maximized": True,
    }
    reply = _launch_qt_gui(request)
    if reply is None:
        error("Unified graphical video editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        try:
            margins = reply.get("margins") or [0, 0, 0, 0]
            top, left, right, bottom = (int(x) for x in margins)
            keep_ranges: list[tuple[float, float]] = []
            for entry in reply.get("keep_ranges") or []:
                s, e = float(entry[0]), float(entry[1])
                if e > s:
                    keep_ranges.append((s, e))
            return {
                "margins": (top, left, right, bottom),
                "keep_ranges": normalize_cut_ranges(keep_ranges, duration),
                "speed": clamp_speed_factor(reply.get("speed", DEFAULT_SPEED_FACTOR)),
                "reverse": bool(reply.get("reverse")),
                "include_audio": bool(reply.get("include_audio", True)),
            }
        except Exception as exc:
            error(f"Unified video editor returned invalid data: {exc}")
            return None
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "video_unified"
        error("Unified video GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


def open_audio_speed_gui(answers: dict[str, Any], audio_index: int) -> dict[str, Any] | None:
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "audio_speed",
        "input_path": str(answers["input_path"]),
        "audio_index": int(audio_index),
        "duration": float(duration),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = _launch_qt_gui(request)
    if reply is None:
        error("Graphical audio speed editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        try:
            return {
                "speed": clamp_speed_factor(reply.get("speed", DEFAULT_SPEED_FACTOR)),
                "reverse": bool(reply.get("reverse")),
            }
        except ValueError as exc:
            error(str(exc))
            return None
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "audio_speed"
        error("Audio speed GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


def open_audio_transform_gui(answers: dict[str, Any], audio_index: int) -> dict[str, Any] | None:
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "audio_transform",
        "input_path": str(answers["input_path"]),
        "audio_index": int(audio_index),
        "duration": float(duration),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
        "start_maximized": True,
    }
    reply = _launch_qt_gui(request)
    if reply is None:
        error("Graphical audio transform editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        try:
            ranges: list[tuple[float, float]] = []
            for entry in reply.get("keep_ranges") or []:
                s, e = float(entry[0]), float(entry[1])
                if e > s:
                    ranges.append((s, e))
            return {
                "keep_ranges": normalize_cut_ranges(ranges, duration),
                "speed": clamp_speed_factor(reply.get("speed", DEFAULT_SPEED_FACTOR)),
                "reverse": bool(reply.get("reverse")),
            }
        except Exception as exc:
            error(f"Audio transform editor returned invalid data: {exc}")
            return None
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "audio_transform"
        error("Audio transform GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


def open_audio_cut_gui(answers: dict[str, Any], audio_index: int) -> list[tuple[float, float]] | None:
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "audio_cut",
        "input_path": str(answers["input_path"]),
        "audio_index": int(audio_index),
        "duration": float(duration),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = _launch_qt_gui(request)
    if reply is None:
        error("Graphical audio cut editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        ranges = reply.get("keep_ranges") or []
        normalized: list[tuple[float, float]] = []
        for entry in ranges:
            try:
                s, e = float(entry[0]), float(entry[1])
            except Exception:
                continue
            if e > s:
                normalized.append((s, e))
        return normalize_cut_ranges(normalized, duration)
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "audio_cut"
        error("Audio cut GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


def _open_legacy_cut_gui_tk(
    answers: dict[str, Any],
    fps: float,
    duration: float,
) -> list[tuple[float, float]] | None:
    """Premiere-inspired Cut Editor (legacy Tk fallback).

    Layout (top -> bottom):
        - Toolbar / header band with title and clip stats.
        - Large preview viewport.
        - "Now / In / Out / Kept / Cuts / Zoom" status strip.
        - Wide timeline with high-contrast ticks, markers, cut ranges.
        - Transport row (play/pause/seek/Mark In/Mark Out/cuts).
        - Tool row (timeline zoom buttons + audio mute + volume slider).
        - Cut-ranges-to-remove list.

    Returns the final list of keep ranges (the inverse of the cut ranges) or
    None on cancel. Cancel from this GUI does NOT abort the cut workflow;
    the caller loops back to the cut-method menu.
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:
        error(f"Tkinter is not available, so the cut GUI cannot be opened: {exc}")
        return None

    if duration <= 0:
        error("Cannot open the cut GUI: source duration is unknown.")
        return None

    palette = _UIPalette
    ffplay = shutil.which("ffplay")
    input_path: Path = answers["input_path"]

    try:
        with tempfile.TemporaryDirectory(prefix="ffmwizard_cut_") as temp_name:
            temp_dir = Path(temp_name)
            source_width, source_height = (1280, 720)
            try:
                source_width, source_height = first_video_size(answers)
            except Exception:
                pass
            preview_w, preview_h = preview_size(source_width, source_height)
            preview_w = max(320, min(960, preview_w))
            preview_h = max(180, round(preview_w * source_height / source_width))

            timeline_default_width = max(preview_w + 240, 920)
            timeline_height = 112

            state: dict[str, Any] = {
                "timestamp": 0.0,
                "in_marker": 0.0,
                "out_marker": min(5.0, duration),
                "cut_ranges": [],            # list of (start_s, end_s) REMOVE
                "selected_cut": -1,
                "playing": False,
                "audio_proc": None,
                "photo": None,
                "photo_key": None,
                "drag_target": None,         # ("playhead",) | ("in",) | ("out",) | ("cut", idx)
                # Timeline zoom: visible window expressed in seconds.
                "view_start_s": 0.0,
                "view_span_s": float(duration),
                # Audio.
                "volume": 50,
                "mute": False,
            }

            result: dict[str, list[tuple[float, float]] | None] = {"keep_ranges": None}

            root = tk.Tk()
            root.title("FFmWiz Cut Editor")
            root.configure(bg=palette.BG)
            _apply_tk_window_icon(root)
            _apply_app_ttk_theme(root)
            _apply_dark_title_bar(root)
            load_icon = _make_icon_loader(root)

            shell = ttk.Frame(root, padding=(16, 0, 16, 12))
            shell.pack(fill="both", expand=True)

            # ---- Header band ----------------------------------------------
            header = ttk.Frame(shell, style="Panel.TFrame", padding=(14, 12, 14, 12))
            header.pack(side="top", fill="x", pady=(8, 10))
            ttk.Label(
                header,
                text="FFmWiz Cut Editor",
                style="Title.TLabel",
            ).pack(side="left")
            ttk.Label(
                header,
                text=(
                    f"FPS {fps:.3f}    •    "
                    f"Duration {seconds_to_ffmpeg_time(duration)}    •    "
                    f"Source {Path(input_path).name}"
                ),
                style="Header.TLabel",
            ).pack(side="right")

            # ---- Preview viewport -----------------------------------------
            preview = tk.Canvas(
                shell,
                width=preview_w,
                height=preview_h,
                bg=palette.TIMELINE_BG,
                highlightthickness=1,
                highlightbackground=palette.BORDER,
            )
            preview.pack(side="top", fill="x", expand=False, pady=(0, 8))

            info_label = ttk.Label(shell, text="", anchor="w", style="Muted.TLabel")
            info_label.pack(side="top", fill="x", pady=(0, 4))

            # ---- Status strip (above the timeline) ------------------------
            status_strip = ttk.Frame(shell, style="Panel.TFrame", padding=(14, 8, 14, 8))
            status_strip.pack(side="top", fill="x", pady=(2, 6))
            time_label = ttk.Label(status_strip, text="", style="Header.TLabel")
            time_label.pack(side="left")

            # ---- Timeline -------------------------------------------------
            timeline_wrap = ttk.Frame(shell, style="Surface.TFrame", padding=(2, 2, 2, 2))
            timeline_wrap.pack(side="top", fill="x", expand=False, pady=(0, 8))
            timeline = tk.Canvas(
                timeline_wrap,
                width=timeline_default_width,
                height=timeline_height,
                bg=palette.TIMELINE_BG,
                highlightthickness=0,
            )
            timeline.pack(side="top", fill="x", expand=True)

            controls = ttk.Frame(shell)
            controls.pack(side="top", fill="x", pady=(2, 0))
            audio_row = ttk.Frame(shell)
            audio_row.pack(side="top", fill="x", pady=(8, 0))

            cut_list_frame = ttk.Frame(shell)
            cut_list_frame.pack(side="top", fill="both", expand=False, pady=(10, 4))
            ttk.Label(cut_list_frame, text="Cut ranges to remove:", style="Dim.TLabel").pack(side="top", anchor="w")
            cut_listbox = tk.Listbox(
                cut_list_frame,
                bg=palette.TIMELINE_BG,
                fg=palette.TEXT,
                selectbackground=palette.ACCENT_DARK,
                selectforeground=palette.TEXT,
                height=5,
                highlightthickness=1,
                highlightbackground=palette.BORDER,
                bd=0,
                activestyle="none",
                font=("Consolas", 10),
            )
            cut_listbox.pack(side="top", fill="x", expand=True)

            # --- Preview frame extraction (async, debounced) -----------------
            def _extract(timestamp: float, w: int, h: int) -> Path:
                return extract_crop_preview_frame(answers, temp_dir, w, h, timestamp)

            def _on_frame_ready(path: Path) -> None:
                try:
                    state["photo"] = tk.PhotoImage(file=str(path))
                except Exception:
                    return
                redraw_preview_canvas()

            scheduler = _PreviewScheduler(
                root,
                extract_fn=_extract,
                on_ready=_on_frame_ready,
                debounce_ms=70,
            )

            # --- Timeline math (zoomable) ------------------------------------
            def _view_start() -> float:
                return float(state["view_start_s"])

            def _view_span() -> float:
                return max(0.001, float(state["view_span_s"]))

            def _view_end() -> float:
                return _view_start() + _view_span()

            def timeline_width() -> int:
                return max(1, int(timeline.winfo_width()) or timeline_default_width)

            def time_to_x(seconds: float) -> int:
                width = timeline_width()
                pad = 12
                usable = max(1, width - pad * 2)
                start = _view_start()
                span = _view_span()
                return int(pad + ((seconds - start) / span) * usable)

            def x_to_time(x_pos: float) -> float:
                width = timeline_width()
                pad = 12
                usable = max(1, width - pad * 2)
                start = _view_start()
                span = _view_span()
                ratio = max(0.0, min(1.0, (x_pos - pad) / usable))
                return start + ratio * span

            def clamp_view() -> None:
                span = min(duration, max(0.05, _view_span()))
                start = max(0.0, min(duration - span, _view_start()))
                state["view_span_s"] = span
                state["view_start_s"] = start

            def zoom_around(focus_time: float, factor: float) -> None:
                """Zoom timeline by 'factor' (<1 zoom in, >1 zoom out), keeping
                focus_time at the same screen position."""
                width = timeline_width()
                pad = 12
                usable = max(1, width - pad * 2)
                # Current screen-x of focus_time before zooming.
                start = _view_start()
                span = _view_span()
                ratio = (focus_time - start) / max(0.001, span)
                new_span = max(0.05, min(duration, span * factor))
                new_start = focus_time - ratio * new_span
                state["view_span_s"] = new_span
                state["view_start_s"] = new_start
                clamp_view()
                redraw_all()

            def zoom_in(_event: Any = None) -> None:
                zoom_around(state["timestamp"], 0.5)

            def zoom_out(_event: Any = None) -> None:
                zoom_around(state["timestamp"], 2.0)

            def zoom_fit(_event: Any = None) -> None:
                state["view_start_s"] = 0.0
                state["view_span_s"] = duration
                redraw_all()

            def timeline_wheel(event: Any) -> str:
                # Wheel = zoom around cursor. Ctrl+wheel = finer zoom.
                step = 1.25 if (event.state & 0x0004) else 1.6
                factor = 1.0 / step if event.delta > 0 else step
                focus_time = x_to_time(event.x)
                zoom_around(focus_time, factor)
                return "break"

            # --- Rendering ---------------------------------------------------
            def refresh_cut_listbox() -> None:
                cut_listbox.delete(0, tk.END)
                for idx, (start, end) in enumerate(state["cut_ranges"]):
                    line = (
                        f"{idx + 1:2d}. {seconds_to_hmsf(start, fps)} -> "
                        f"{seconds_to_hmsf(end, fps)}   "
                        f"({seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)})"
                    )
                    cut_listbox.insert(tk.END, line)
                if 0 <= state["selected_cut"] < len(state["cut_ranges"]):
                    cut_listbox.selection_clear(0, tk.END)
                    cut_listbox.selection_set(state["selected_cut"])

            def refresh_time_label() -> None:
                kept = duration - sum(max(0.0, e - s) for s, e in state["cut_ranges"])
                zoom_pct = int(round(100.0 * duration / max(0.001, _view_span())))
                # Compact, high-contrast status strip. We use Unicode "Big dot"
                # separators so individual fields stay readable.
                time_label.configure(text=(
                    f"●  Now {seconds_to_hmsf(state['timestamp'], fps)}    "
                    f"{seconds_to_ffmpeg_time(state['timestamp'])} / "
                    f"{seconds_to_ffmpeg_time(duration)}        "
                    f"●  In {seconds_to_hmsf(state['in_marker'], fps)}    "
                    f"●  Out {seconds_to_hmsf(state['out_marker'], fps)}    "
                    f"●  Kept {format_duration(kept)}    "
                    f"●  Cuts {len(state['cut_ranges'])}    "
                    f"●  Zoom {zoom_pct}%"
                ))

            def redraw_preview_canvas() -> None:
                preview.delete("all")
                cached = scheduler.request(state["timestamp"], preview_w, preview_h)
                if cached is not None:
                    try:
                        state["photo"] = tk.PhotoImage(file=str(cached))
                    except Exception:
                        pass
                if state.get("photo") is not None:
                    preview.create_image(preview_w // 2, preview_h // 2, image=state["photo"])
                else:
                    preview.create_text(
                        preview_w // 2,
                        preview_h // 2,
                        text="(loading preview frame...)",
                        fill=palette.TEXT_MUTE,
                        font=("Segoe UI", 11),
                    )

            def redraw_timeline() -> None:
                timeline.delete("all")
                width = timeline_width()
                height = timeline_height
                # Larger, more breathable layout than the original.
                label_strip_top = 6
                label_strip_bottom = 24
                track_top = 30
                track_bottom = height - 18
                track_mid = (track_top + track_bottom) // 2
                pad = 14
                # Frame around the timeline so it visually reads as a panel.
                timeline.create_rectangle(
                    0, 0, width, height,
                    fill=palette.TIMELINE_BG, outline="",
                )
                timeline.create_rectangle(
                    pad - 2, track_top, width - pad + 2, track_bottom,
                    fill=palette.TIMELINE_TRACK, outline=palette.BORDER, width=1,
                )
                # Choose a tick step that yields ~7-10 labels regardless of zoom.
                span = _view_span()
                approx_step = span / 8.0
                exponent = math.floor(math.log10(max(approx_step, 0.001)))
                base = 10 ** exponent
                step = base
                for candidate in (1, 2, 5, 10):
                    step = candidate * base
                    if span / step <= 10:
                        break
                start = _view_start()
                end = _view_end()
                first_tick = math.ceil(start / step) * step
                t = first_tick
                tick_font = ("Segoe UI Semibold", 10)
                sub_font = ("Segoe UI", 8)
                while t <= end + 1e-6:
                    x_pos = time_to_x(t)
                    if pad <= x_pos <= width - pad:
                        timeline.create_line(
                            x_pos, track_top - 6, x_pos, track_top,
                            fill=palette.TIMELINE_TICK_HI, width=1,
                        )
                        timeline.create_text(
                            x_pos, label_strip_top + (label_strip_bottom - label_strip_top) // 2,
                            text=seconds_to_ffmpeg_time(t),
                            fill=palette.TIMELINE_TICK_HI,
                            font=tick_font,
                        )
                    t += step
                # Secondary minor ticks (no label) at step / 5.
                minor_step = step / 5
                if minor_step > 0:
                    t = math.ceil(start / minor_step) * minor_step
                    while t <= end + 1e-6:
                        x_pos = time_to_x(t)
                        if pad <= x_pos <= width - pad:
                            timeline.create_line(
                                x_pos, track_top - 3, x_pos, track_top,
                                fill=palette.TIMELINE_TICK, width=1,
                            )
                        t += minor_step
                # Removed cut ranges (red boxes).
                for idx, (cstart, cend) in enumerate(state["cut_ranges"]):
                    if cend < start or cstart > end:
                        continue
                    x1 = time_to_x(max(cstart, start))
                    x2 = time_to_x(min(cend, end))
                    fill = palette.ACCENT_RED if idx == state["selected_cut"] else palette.ACCENT_RED_DK
                    timeline.create_rectangle(
                        x1, track_top + 3, x2, track_bottom - 3,
                        fill=fill, outline=palette.BORDER, width=1, tags=("cut", str(idx)),
                    )
                    if x2 - x1 > 36:
                        timeline.create_text(
                            (x1 + x2) // 2,
                            track_mid,
                            text=f"#{idx + 1}",
                            fill=palette.TEXT,
                            font=("Segoe UI Semibold", 10),
                            tags=("cut", str(idx)),
                        )
                # In / Out markers.
                in_x = time_to_x(state["in_marker"])
                out_x = time_to_x(state["out_marker"])
                marker_font = ("Segoe UI Semibold", 9)
                if pad - 8 <= in_x <= width - pad + 8:
                    timeline.create_line(in_x, track_top - 4, in_x, track_bottom + 4,
                                         fill=palette.ACCENT_GREEN, width=3, tags=("marker", "in"))
                    timeline.create_polygon(
                        in_x, track_top - 4, in_x - 8, track_top - 14, in_x + 8, track_top - 14,
                        fill=palette.ACCENT_GREEN, outline=palette.BG, tags=("marker", "in"),
                    )
                    timeline.create_text(in_x + 12, track_top - 9, anchor="w",
                                         text="IN", fill=palette.ACCENT_GREEN, font=marker_font)
                if pad - 8 <= out_x <= width - pad + 8:
                    timeline.create_line(out_x, track_top - 4, out_x, track_bottom + 4,
                                         fill=palette.ACCENT_YELLOW, width=3, tags=("marker", "out"))
                    timeline.create_polygon(
                        out_x, track_top - 4, out_x - 8, track_top - 14, out_x + 8, track_top - 14,
                        fill=palette.ACCENT_YELLOW, outline=palette.BG, tags=("marker", "out"),
                    )
                    timeline.create_text(out_x - 12, track_top - 9, anchor="e",
                                         text="OUT", fill=palette.ACCENT_YELLOW, font=marker_font)
                # Playhead.
                ph_x = time_to_x(state["timestamp"])
                if pad - 6 <= ph_x <= width - pad + 6:
                    timeline.create_line(ph_x, track_top - 10, ph_x, track_bottom + 10,
                                         fill=palette.PLAYHEAD, width=2, tags=("playhead",))
                    timeline.create_polygon(
                        ph_x - 7, track_bottom + 4,
                        ph_x + 7, track_bottom + 4,
                        ph_x, track_bottom + 14,
                        fill=palette.PLAYHEAD, outline=palette.BG, tags=("playhead",),
                    )
                    timeline.create_text(
                        ph_x, label_strip_bottom - 4,
                        text=seconds_to_ffmpeg_time(state["timestamp"]),
                        fill=palette.PLAYHEAD,
                        font=sub_font,
                        tags=("playhead",),
                    )

            def redraw_all() -> None:
                redraw_preview_canvas()
                redraw_timeline()
                refresh_cut_listbox()
                refresh_time_label()

            # --- Audio playback (ffplay) -------------------------------------
            def stop_audio() -> None:
                proc = state.get("audio_proc")
                state["audio_proc"] = None
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=0.8)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass

            def start_audio() -> None:
                stop_audio()
                if not ffplay or state["mute"] or int(state["volume"]) <= 0:
                    return
                args = [
                    ffplay, "-nodisp", "-autoexit", "-loglevel", "error",
                    "-ss", f"{state['timestamp']:.3f}",
                    "-volume", str(int(state["volume"])),
                    str(input_path),
                ]
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                try:
                    state["audio_proc"] = subprocess.Popen(
                        args,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creation_flags,
                    )
                except Exception as exc:
                    error(f"Could not start ffplay audio preview: {exc}")

            def playback_tick() -> None:
                if not state["playing"]:
                    return
                state["timestamp"] = min(duration, state["timestamp"] + 1.0)
                redraw_all()
                if state["timestamp"] >= duration - 1e-6:
                    # Reached the end. Stop playback cleanly so the next Play
                    # press can rewind to the beginning (see toggle_playback).
                    state["playing"] = False
                    play_button.configure(text="▶ Play (Space)")
                    stop_audio()
                    return
                root.after(1000, playback_tick)

            def toggle_playback(_event: Any = None) -> None:
                # Bug fix: if playback is at or near the end and the user
                # presses Play again, rewind to the start so playback runs
                # for the whole clip instead of ending after ~1 second.
                if not state["playing"] and state["timestamp"] >= duration - 0.5:
                    state["timestamp"] = 0.0
                    redraw_all()
                state["playing"] = not state["playing"]
                play_button.configure(text=("⏸ Pause (Space)" if state["playing"] else "▶ Play (Space)"))
                if state["playing"]:
                    start_audio()
                    root.after(1000, playback_tick)
                else:
                    stop_audio()

            def set_time(value: float) -> None:
                state["timestamp"] = max(0.0, min(duration, float(value)))
                redraw_all()
                if state["playing"]:
                    start_audio()

            def go_home(_event: Any = None) -> None:
                set_time(0.0)

            def go_end(_event: Any = None) -> None:
                set_time(duration)

            def set_in_marker(_event: Any = None) -> None:
                state["in_marker"] = state["timestamp"]
                if state["out_marker"] < state["in_marker"]:
                    state["out_marker"] = state["in_marker"]
                redraw_all()

            def set_out_marker(_event: Any = None) -> None:
                state["out_marker"] = state["timestamp"]
                if state["in_marker"] > state["out_marker"]:
                    state["in_marker"] = state["out_marker"]
                redraw_all()

            def add_cut_range(_event: Any = None) -> None:
                start = min(state["in_marker"], state["out_marker"])
                end = max(state["in_marker"], state["out_marker"])
                if end - start < 1e-3:
                    error("Cut range is empty; place In and Out at different positions first.")
                    return
                state["cut_ranges"].append((start, end))
                state["cut_ranges"] = list(normalize_cut_ranges(state["cut_ranges"], duration))
                state["selected_cut"] = len(state["cut_ranges"]) - 1
                redraw_all()

            def remove_selected_cut(_event: Any = None) -> None:
                idx = state["selected_cut"]
                if 0 <= idx < len(state["cut_ranges"]):
                    del state["cut_ranges"][idx]
                    state["selected_cut"] = min(idx, len(state["cut_ranges"]) - 1)
                    redraw_all()

            def clear_all_cuts(_event: Any = None) -> None:
                state["cut_ranges"] = []
                state["selected_cut"] = -1
                redraw_all()

            def confirm(_event: Any = None) -> None:
                keep = invert_cut_ranges_to_keep_ranges(state["cut_ranges"], duration)
                if not state["cut_ranges"]:
                    start = min(state["in_marker"], state["out_marker"])
                    end = max(state["in_marker"], state["out_marker"])
                    if end - start > 1e-3:
                        keep = [(start, end)]
                    else:
                        keep = [(0.0, duration)]
                result["keep_ranges"] = keep
                state["playing"] = False
                stop_audio()
                scheduler.cancel()
                root.destroy()

            def cancel(_event: Any = None) -> None:
                result["keep_ranges"] = None
                state["playing"] = False
                stop_audio()
                scheduler.cancel()
                root.destroy()

            def on_listbox_select(_event: Any) -> None:
                sel = cut_listbox.curselection()
                state["selected_cut"] = sel[0] if sel else -1
                redraw_timeline()

            # --- Timeline drag handlers --------------------------------------
            def begin_timeline_drag(event: Any) -> None:
                items = timeline.find_overlapping(event.x - 4, event.y - 4, event.x + 4, event.y + 4)
                target = None
                for item_id in reversed(items):
                    tags = timeline.gettags(item_id)
                    if "marker" in tags and "in" in tags:
                        target = ("in",)
                        break
                    if "marker" in tags and "out" in tags:
                        target = ("out",)
                        break
                    if "playhead" in tags:
                        target = ("playhead",)
                        break
                    if "cut" in tags:
                        idx = int(tags[tags.index("cut") + 1])
                        state["selected_cut"] = idx
                        refresh_cut_listbox()
                        target = ("cut", idx)
                        break
                if target is None:
                    set_time(x_to_time(event.x))
                    target = ("playhead",)
                state["drag_target"] = target

            def drag_timeline(event: Any) -> None:
                target = state.get("drag_target")
                if not target:
                    return
                t = x_to_time(event.x)
                if target[0] == "playhead":
                    set_time(t)
                elif target[0] == "in":
                    state["in_marker"] = max(0.0, min(duration, t))
                    if state["out_marker"] < state["in_marker"]:
                        state["out_marker"] = state["in_marker"]
                    redraw_all()
                elif target[0] == "out":
                    state["out_marker"] = max(0.0, min(duration, t))
                    if state["in_marker"] > state["out_marker"]:
                        state["in_marker"] = state["out_marker"]
                    redraw_all()
                elif target[0] == "cut":
                    idx = target[1]
                    if 0 <= idx < len(state["cut_ranges"]):
                        start, end = state["cut_ranges"][idx]
                        span = end - start
                        new_start = max(0.0, min(duration - span, t - span / 2))
                        state["cut_ranges"][idx] = (new_start, new_start + span)
                        redraw_all()

            def end_timeline_drag(_event: Any) -> None:
                if state.get("drag_target") and state["drag_target"][0] == "cut":
                    state["cut_ranges"] = list(normalize_cut_ranges(state["cut_ranges"], duration))
                state["drag_target"] = None

            # --- Audio controls ----------------------------------------------
            volume_var = tk.IntVar(value=int(state["volume"]))
            mute_var = tk.BooleanVar(value=bool(state["mute"]))

            def volume_level_index() -> int:
                v = int(volume_var.get())
                if mute_var.get() or v <= 0:
                    return 0
                if v < 34:
                    return 1
                if v < 67:
                    return 2
                return 3

            def refresh_audio_label() -> None:
                lvl = volume_level_index()
                icon = volume_icons.get(lvl)
                mute_text = "Muted (M)" if state["mute"] else "Mute (M)"
                if icon is not None:
                    mute_btn.configure(text=mute_text, image=icon, compound="left")
                else:
                    mute_btn.configure(text=("🔇 " + mute_text if state["mute"] else "🔊 " + mute_text))
                volume_label.configure(text=f"Vol {int(volume_var.get()):3d}%")

            def on_volume_change(_value: Any = None) -> None:
                state["volume"] = max(0, min(100, int(volume_var.get())))
                if state["playing"]:
                    start_audio()
                refresh_audio_label()

            def toggle_mute(_event: Any = None) -> None:
                state["mute"] = not state["mute"]
                mute_var.set(state["mute"])
                if state["playing"]:
                    start_audio()
                refresh_audio_label()

            def volume_wheel(event: Any) -> str:
                step = 3 if (event.state & 0x0004) else 5
                if event.delta > 0:
                    new_val = min(100, int(volume_var.get()) + step)
                else:
                    new_val = max(0, int(volume_var.get()) - step)
                volume_var.set(new_val)
                on_volume_change()
                return "break"

            # --- Icons -------------------------------------------------------
            zoom_in_icon = load_icon("zoom_in")
            zoom_out_icon = load_icon("zoom_out")
            volume_icons = {
                0: load_icon("volume_0"),
                1: load_icon("volume_1"),
                2: load_icon("volume_2"),
                3: load_icon("volume_3"),
            }

            # --- Transport row (buttons) ------------------------------------
            play_button = ttk.Button(
                controls, text="▶ Play (Space)", command=toggle_playback, style="Accent.TButton",
            )
            play_button.pack(side="left")
            ttk.Button(controls, text="⏮ Home (Home)", command=go_home).pack(side="left", padx=(8, 0))
            ttk.Button(controls, text="-5s (Shift+←)", command=lambda: set_time(state["timestamp"] - 5.0)).pack(side="left", padx=(8, 0))
            ttk.Button(controls, text="-1s (←)", command=lambda: set_time(state["timestamp"] - 1.0)).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="+1s (→)", command=lambda: set_time(state["timestamp"] + 1.0)).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="+5s (Shift+→)", command=lambda: set_time(state["timestamp"] + 5.0)).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="End (End) ⏭", command=go_end).pack(side="left", padx=(8, 0))
            ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=10)
            ttk.Button(controls, text="Mark In (I)", command=set_in_marker).pack(side="left")
            ttk.Button(controls, text="Mark Out (O)", command=set_out_marker).pack(side="left", padx=(4, 0))
            ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=10)
            ttk.Button(controls, text="Add cut (A)", command=add_cut_range).pack(side="left")
            ttk.Button(controls, text="Delete cut (Del)", command=remove_selected_cut).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="Clear all", command=clear_all_cuts).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="Confirm (Enter)", command=confirm, style="Accent.TButton").pack(side="right")
            ttk.Button(controls, text="Cancel (Esc)", command=cancel, style="Danger.TButton").pack(side="right", padx=(0, 8))

            # --- Audio + zoom row -------------------------------------------
            zoom_in_btn = ttk.Button(audio_row, text="Zoom In (+)", command=zoom_in)
            if zoom_in_icon is not None:
                zoom_in_btn.configure(image=zoom_in_icon, compound="left")
            zoom_in_btn.pack(side="left")
            zoom_out_btn = ttk.Button(audio_row, text="Zoom Out (-)", command=zoom_out)
            if zoom_out_icon is not None:
                zoom_out_btn.configure(image=zoom_out_icon, compound="left")
            zoom_out_btn.pack(side="left", padx=(6, 0))
            ttk.Button(audio_row, text="Reset Zoom (Ctrl+R)", command=zoom_fit).pack(side="left", padx=(6, 0))
            ttk.Separator(audio_row, orient="vertical").pack(side="left", fill="y", padx=12)
            mute_btn = ttk.Button(audio_row, text="Mute (M)", command=toggle_mute)
            mute_btn.pack(side="left")
            volume_label = ttk.Label(audio_row, text="Vol  50%", style="Dim.TLabel")
            volume_label.pack(side="left", padx=(10, 6))
            volume_slider = ttk.Scale(
                audio_row,
                from_=0,
                to=100,
                orient="horizontal",
                variable=volume_var,
                command=on_volume_change,
                length=200,
            )
            volume_slider.pack(side="left")
            # Mouse-wheel volume support when hovering the slider OR the
            # label/mute button (so users have a clear hover target).
            for widget in (volume_slider, volume_label, mute_btn):
                widget.bind("<MouseWheel>", volume_wheel)
            if not ffplay:
                volume_slider.state(["disabled"])
                mute_btn.state(["disabled"])
                ttk.Label(audio_row, text="(ffplay not in PATH; audio preview disabled)",
                          style="Muted.TLabel").pack(side="left", padx=(10, 0))

            info_label.configure(text=(
                "Click or drag the playhead to seek. Mark In (I) and Mark Out (O), "
                "then Add cut (A). Mouse wheel over the timeline zooms around the cursor; "
                "Ctrl+wheel = finer zoom. Mouse wheel over the volume slider changes volume."
            ))

            # --- Timeline mouse bindings ------------------------------------
            timeline.bind("<ButtonPress-1>", begin_timeline_drag)
            timeline.bind("<B1-Motion>", drag_timeline)
            timeline.bind("<ButtonRelease-1>", end_timeline_drag)
            timeline.bind("<MouseWheel>", timeline_wheel)
            timeline.bind("<Configure>", lambda _e: redraw_timeline())
            cut_listbox.bind("<<ListboxSelect>>", on_listbox_select)

            # --- Layout-independent keyboard shortcuts ----------------------
            # Bindings target the physical key (Windows VK code) so they
            # still fire when the active keyboard layout is Persian or any
            # other non-Latin layout.

            def is_editing_text(event: Any) -> bool:
                widget = getattr(event, "widget", None)
                try:
                    cls = str(widget.winfo_class()) if widget is not None else ""
                except Exception:
                    cls = ""
                return cls in ("Entry", "TEntry", "Text", "Spinbox", "TCombobox", "TSpinbox")

            _bind_layout_independent_keys(root, [
                {"key": "space", "callback": toggle_playback},
                {"key": "i", "callback": set_in_marker},
                {"key": "o", "callback": set_out_marker},
                {"key": "a", "callback": add_cut_range},
                {"key": "m", "callback": toggle_mute},
                {"key": "f", "callback": zoom_fit},
                {"key": "r", "ctrl": True, "callback": zoom_fit},
                {"key": "plus", "callback": zoom_in},
                {"key": "minus", "callback": zoom_out},
                {"key": "home", "callback": go_home},
                {"key": "end", "callback": go_end},
                {"key": "left", "shift": False, "callback": lambda _e: set_time(state["timestamp"] - 1.0)},
                {"key": "right", "shift": False, "callback": lambda _e: set_time(state["timestamp"] + 1.0)},
                {"key": "left", "shift": True, "callback": lambda _e: set_time(state["timestamp"] - 5.0)},
                {"key": "right", "shift": True, "callback": lambda _e: set_time(state["timestamp"] + 5.0)},
                {"key": "delete", "callback": remove_selected_cut},
                {"key": "return", "callback": confirm},
                {"key": "escape", "callback": cancel},
            ], is_text_focus_fn=is_editing_text)
            root.protocol("WM_DELETE_WINDOW", cancel)

            root.update_idletasks()
            refresh_audio_label()
            redraw_all()
            root.mainloop()
    except Exception as exc:
        error(f"Cut GUI failed: {exc}")
        return None

    return result.get("keep_ranges")


def output_is_audio_only(answers: dict[str, Any]) -> bool:
    ext = answers.get("output_ext", "").lower()
    if ext in AUDIO_ONLY_EXTS:
        return True
    if not answers.get("video_streams"):
        return True
    return False


def output_has_video(answers: dict[str, Any]) -> bool:
    return bool(answers.get("video_streams")) and not output_is_audio_only(answers)


def default_audio_codec_for_ext(ext: str) -> str:
    return AUDIO_CODEC_DEFAULTS_BY_FORMAT.get(ext.lower(), DEFAULT_AUDIO_CODEC)


def audio_codec_uses_bitrate(codec: str) -> bool:
    lowered = codec.lower()
    if lowered == "copy":
        return False
    if lowered.startswith("pcm_"):
        return False
    return lowered in BITRATE_AUDIO_CODECS


def video_codec_is_copy(answers: dict[str, Any]) -> bool:
    return str(answers.get("video_codec", "")).lower() == "copy"


def video_reencode_options_applicable(answers: dict[str, Any]) -> bool:
    return output_has_video(answers) and (
        not video_codec_is_copy(answers)
        or bool(answers.get("crop_enabled"))
        or video_speed_transform_enabled(answers)
    )


def selected_audio_streams(answers: dict[str, Any]) -> list[int]:
    selected = answers.get("audio_tracks", [])
    count = len(answers.get("audio_streams", []))
    if selected == "all":
        return list(range(count))
    return selected


def audio_only_transform_prompt_applicable(answers: dict[str, Any]) -> bool:
    if output_has_video(answers):
        return False
    if not answers.get("audio_streams"):
        return False
    if "audio_tracks" not in answers:
        return True
    return bool(selected_audio_streams(answers))


def selected_subtitle_streams(answers: dict[str, Any]) -> list[int]:
    selected = answers.get("subtitle_tracks", [])
    count = len(answers.get("subtitle_streams", []))
    if selected == "all":
        return list(range(count))
    return selected


def _folder_validation_items(answers: dict[str, Any]) -> list[dict[str, Any]]:
    items = answers.get("_folder_items")
    return items if isinstance(items, list) else []


def _answers_for_folder_item(settings_answers: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    item_answers = dict(settings_answers)
    copy_media_metadata(item_answers, item.get("answers", {}))
    item_answers["_quiet_packet_size_probe"] = True
    return item_answers


def detected_video_bitrate_limit(answers: dict[str, Any]) -> tuple[int | None, str]:
    values: list[int] = []
    items = _folder_validation_items(answers)
    if items:
        for item in items:
            item_answers = _answers_for_folder_item(answers, item)
            if not item_answers.get("video_streams"):
                continue
            packet_sizes = get_packet_sizes(item_answers)
            value = stream_bitrate_kbps(item_answers["video_streams"][0], item_answers.get("format"), packet_sizes)
            if value:
                values.append(value)
        return (min(values) if values else None), "lowest detected source video bitrate in folder"

    if not answers.get("video_streams"):
        return None, "detected source video bitrate"
    packet_sizes = get_packet_sizes(answers)
    value = stream_bitrate_kbps(answers["video_streams"][0], answers.get("format"), packet_sizes)
    return value, "detected source video bitrate"


def detected_fps_limit(answers: dict[str, Any]) -> tuple[float | None, str]:
    values: list[float] = []
    items = _folder_validation_items(answers)
    if items:
        for item in items:
            item_answers = _answers_for_folder_item(answers, item)
            if not item_answers.get("video_streams"):
                continue
            value = rational_to_float(item_answers["video_streams"][0].get("avg_frame_rate"))
            if value:
                values.append(value)
        return (min(values) if values else None), "lowest detected source FPS in folder"

    if not answers.get("video_streams"):
        return None, "detected source FPS"
    value = rational_to_float(answers["video_streams"][0].get("avg_frame_rate"))
    return value, "detected source FPS"


def detected_resolution_limit(answers: dict[str, Any]) -> tuple[tuple[int, int] | None, str]:
    values: list[tuple[int, int]] = []
    items = _folder_validation_items(answers)
    if items:
        for item in items:
            item_answers = _answers_for_folder_item(answers, item)
            if not item_answers.get("video_streams"):
                continue
            try:
                values.append(cropped_source_size(item_answers))
            except Exception:
                log_exception(f"Could not detect source resolution for folder validation: {item.get('path')}")
        if not values:
            return None, "smallest detected source resolution in folder"
        min_width = min(width for width, _height in values)
        min_height = min(height for _width, height in values)
        return (min_width, min_height), "smallest detected source/cropped resolution in folder"

    if not answers.get("video_streams"):
        return None, "detected source resolution"
    try:
        return cropped_source_size(answers), "detected source/cropped resolution"
    except Exception:
        log_exception("Could not detect source resolution for validation")
        return None, "detected source resolution"


def detected_audio_bitrate_limit(answers: dict[str, Any]) -> tuple[int | None, str]:
    values: list[int] = []
    items = _folder_validation_items(answers)
    if items:
        for item in items:
            item_answers = _answers_for_folder_item(answers, item)
            streams = item_answers.get("audio_streams") or []
            if not streams:
                continue
            if item_answers.get("audio_tracks_mode") in {"d", "e", "de", "ed"}:
                indexes = list(range(len(streams)))
            else:
                indexes = selected_audio_streams(item_answers) if item_answers.get("audio_tracks") is not None else [0]
            packet_sizes = get_packet_sizes(item_answers)
            for index in indexes:
                if 0 <= index < len(streams):
                    value = stream_bitrate_kbps(streams[index], item_answers.get("format"), packet_sizes)
                    if value:
                        values.append(value)
        return (min(values) if values else None), "lowest detected selected audio bitrate in folder"

    streams = answers.get("audio_streams") or []
    selected = selected_audio_streams(answers) if streams else []
    if not selected:
        return None, "detected source audio bitrate"
    packet_sizes = get_packet_sizes(answers)
    first_selected = selected[0]
    if first_selected < 0 or first_selected >= len(streams):
        return None, "detected source audio bitrate"
    value = stream_bitrate_kbps(streams[first_selected], answers.get("format"), packet_sizes)
    return value, "detected selected audio bitrate"


def confirm_target_above_source(
    answers: dict[str, Any],
    setting_label: str,
    target_text: str,
    source_text: str,
    source_label: str,
    consequence: str,
) -> bool:
    prompt = question_prompt(
        answers,
        f"Warning: {setting_label} is higher than source. Continue?",
        f"y/n; target {target_text} > {source_label} {source_text}; {consequence}",
        "n",
    )
    confirmed = ask_yes_no(prompt, False)
    if confirmed:
        log_info(f"User confirmed above-source {setting_label}: target={target_text}; source={source_text}; source_label={source_label}")
    else:
        note(f"{setting_label} was not accepted. Returning to the same question.")
    return confirmed


def confirm_numeric_target_not_above_source(
    answers: dict[str, Any],
    setting_label: str,
    target: float | int,
    source: float | int | None,
    source_label: str,
    unit: str,
    consequence: str,
) -> bool:
    if source is None or float(target) <= float(source):
        return True
    target_text = f"{format(target, '.3g')} {unit}" if isinstance(target, float) else f"{target} {unit}"
    source_text = f"{format(source, '.3g')} {unit}" if isinstance(source, float) else f"{source} {unit}"
    return confirm_target_above_source(answers, setting_label, target_text, source_text, source_label, consequence)


def confirm_resolution_not_above_source(answers: dict[str, Any], resolution: Any) -> bool:
    dimensions, _warning_text = calculate_scale_dimensions(answers, resolution)
    if dimensions is None:
        return True
    source, source_label = detected_resolution_limit(answers)
    if source is None:
        return True
    out_w, out_h = dimensions
    source_w, source_h = source
    if out_w <= source_w and out_h <= source_h:
        return True
    warning_key = f"{out_w}x{out_h}>{source_w}x{source_h}"
    if answers.get("_resolution_upscale_note_emitted") != warning_key:
        note(
            "Resolution note: output resolution "
            f"{out_w}x{out_h} is above {source_label} {source_w}x{source_h}; "
            "this upscales pixels and can increase file size without adding real detail."
        )
        log_info(
            f"Accepted practical resolution upscale: target={out_w}x{out_h}; "
            f"source={source_w}x{source_h}; source_label={source_label}"
        )
        answers["_resolution_upscale_note_emitted"] = warning_key
    return True


def load_input_metadata(answers: dict[str, Any], input_path: Path) -> None:
    probe = ffprobe_json(answers["ffprobe"], input_path)
    streams = probe.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    if not video_streams and not audio_streams:
        raise ValueError("This file has no detectable video or audio streams.")

    answers["input_path"] = input_path
    answers["probe"] = probe
    answers["format"] = probe.get("format", {})
    answers["video_streams"] = video_streams
    answers["audio_streams"] = audio_streams
    answers["subtitle_streams"] = subtitle_streams
    answers.pop("packet_sizes", None)
    answers.pop("audio_duplicate_report", None)


def apply_output_location_value(answers: dict[str, Any], value: str) -> None:
    input_path: Path = answers["input_path"]
    answers.pop("output_name_stem", None)
    answers["output_used_default"] = False
    if not value:
        # The default output folder is the input folder. The previous default
        # was a fixed E:\output path which broke most workflows.
        answers["output_location"] = input_path.parent
        answers["output_used_default"] = True
        return

    output_value = terminal_path(value)
    if not output_value.drive and not output_value.root and output_value.parent == Path(".") and not output_value.suffix:
        answers["output_location"] = input_path.parent
        answers["output_name_stem"] = output_value.name
    else:
        answers["output_location"] = output_value


INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_output_stem(stem: str) -> str:
    """Sanitize only the filename stem, preserving folders and extensions."""
    cleaned = INVALID_FILENAME_CHARS_RE.sub("_", str(stem or "")).strip(" .")
    return cleaned or "output"


def paths_same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def unique_numbered_path(path: Path) -> Path:
    if not path.exists():
        return path
    for counter in range(2, 10000):
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a free output filename near: {path}")


def resolve_output_collision(output_path: Path, input_path: Path, collision_suffix: str) -> Path:
    """Avoid writing over the source file when output name and extension match."""
    if not paths_same(output_path, input_path):
        return output_path
    safe_stem = sanitize_output_stem(output_path.stem)
    candidate = output_path.with_name(f"{safe_stem}{collision_suffix}{output_path.suffix}")
    return unique_numbered_path(candidate)


def _append_cut_suffix(path: Path) -> Path:
    """Backward-compatible helper for callers that need an explicit cut suffix."""
    return unique_numbered_path(path.with_name(f"{sanitize_output_stem(path.stem)}_cut{path.suffix}"))


def step_input_path(answers: dict[str, Any]) -> None:
    while True:
        input_example = example_text('"E:\\Input\\video.mkv"')
        value = ask_required(
            question_prompt(
                answers,
                "Enter input file path",
                f"drag and drop a file here or paste a path; example: {input_example}",
            )
        )
        input_path = terminal_path(value)
        if not input_path.exists() or not input_path.is_file():
            error("File not found. Enter the full file path again.")
            continue

        try:
            load_input_metadata(answers, input_path)
        except FFprobeError as exc:
            error(str(exc))
            continue
        except Exception as exc:
            log_exception(f"ffprobe metadata load failed for input path: {input_path}")
            error(f"ffprobe could not read the file. See log file: {_log_file_text()}")
            continue
        return


def step_output_location(answers: dict[str, Any]) -> None:
    folder_example = example_text(r"E:\output")
    name_example = example_text('"File name"')
    value = ask_raw(
        question_prompt(
            answers,
            "Enter output path, output folder, or bare output name",
            f"Enter=same folder as input; examples: {folder_example} or {name_example}",
        )
    )
    if value == "0":
        raise Back()
    apply_output_location_value(answers, value)
    print_source_info(answers)


def step_output_format(answers: dict[str, Any]) -> None:
    input_ext = answers["input_path"].suffix.lstrip(".") or "mp4"
    default_ext = "mp4" if answers.get("video_streams") else "mp3"
    common_formats = COMMON_VIDEO_FORMATS + COMMON_AUDIO_FORMATS
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Enter final output format",
                f"common: {option_list(common_formats)}; {keep_value_text(f'n=Use input format ({input_ext})')}",
                default_ext,
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = default_ext
        try:
            answers["output_format_keep_input"] = value.lower().strip() == "n"
            answers["output_ext"] = normalize_format(value, input_ext)
            return
        except ValueError as exc:
            error(str(exc))


def step_video_codec(answers: dict[str, Any]) -> None:
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Enter video codec",
                f"common: {option_list(COMMON_VIDEO_CODECS)}; {keep_value_text('n=copy current video stream without re-encoding')}",
                DEFAULT_VIDEO_CODEC,
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = DEFAULT_VIDEO_CODEC
        if value.lower() == "n":
            value = "copy"
        lowered = value.lower()
        if lowered == "copy" or lowered in VIDEO_CODEC_ALIASES:
            answers["video_codec"] = value
            return
        available = {str(item).lower() for item in answers.get("video_encoders") or []}
        if not available or lowered in available:
            answers["video_codec"] = value
            return
        error(
            f"Unknown video encoder '{value}'. Enter one of the common aliases "
            "or an encoder reported by your FFmpeg build."
        )


def step_use_gpu(answers: dict[str, Any]) -> None:
    answers["use_gpu"] = ask_yes_no(
        question_prompt(answers, "Use GPU/NVIDIA for decode/filter/encode?", "y/n", "y"),
        True,
    )


def step_unified_video_editor_for_encode(answers: dict[str, Any]) -> None:
    if answers.get("_disable_graphical_editors"):
        answers["_unified_video_editor_used"] = False
        return
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Open unified graphical video editor?",
                f"y/n, {graphical_hint('g=Show Unified Video Editor')}; combines crop, cuts, speed/reverse, and audio waveform preview",
                "n",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["_unified_video_editor_used"] = False
            return
        if lowered in {"y", "yes", "g", "gui", "graphical"}:
            note("Loading Unified Graphical Video Editor...")
            sys.stdout.flush()
            result = open_unified_video_gui(answers)
            if result is None:
                note("Unified graphical video editor was canceled. Returning to the unified editor question.")
                continue
            top, left, right, bottom = result["margins"]
            if not set_crop_margins_if_valid(answers, top, left, right, bottom):
                continue
            answers["_unified_video_editor_used"] = True
            answers["_unified_cut_keep_ranges"] = result.get("keep_ranges") or []
            answers["_unified_video_speed"] = result["speed"]
            answers["_unified_reverse_video"] = result["reverse"]
            answers["_unified_include_audio"] = result["include_audio"]
            print(paint("Unified graphical edits captured.", Color.LIME))
            return
        error("Enter y, n, or g.")


def step_crop_enabled(answers: dict[str, Any]) -> None:
    if answers.get("_unified_video_editor_used"):
        margins = (
            int(answers.get("crop_top", 0) or 0),
            int(answers.get("crop_left", 0) or 0),
            int(answers.get("crop_right", 0) or 0),
            int(answers.get("crop_bottom", 0) or 0),
        )
        answers["crop_enabled"] = any(margins)
        answers["crop_values_inline"] = bool(any(margins))
        if any(margins):
            print(paint(f"Applied unified crop: {format_crop_margins(answers)}", Color.LIME))
        return
    while True:
        # Highlight the graphical crop editor shortcut so it stands out from
        # the rest of the hint text. The outer paint() wraps the whole hint
        # in HINT_YELLOW; we re-apply HINT_YELLOW after the inner aqua span
        # so the trailing text restores the original color.
        allow_gui = not answers.get("_disable_graphical_editors")
        if allow_gui:
            if USE_COLOR:
                gui_hint = (
                    f"{Color.AQUA}g=Show Graphical Crop Editor{Color.RESET}{Color.HINT_YELLOW}"
                )
            else:
                gui_hint = "g=Show Graphical Crop Editor"
            crop_hint = (
                f"y/n, {gui_hint}, or inline top,left,right,bottom like "
                f"{example_text('100,300,200,550')}; {paint('zero is allowed inside inline crop', Color.ZERO_INLINE)}"
            )
        else:
            crop_hint = (
                "y/n, or inline top,left,right,bottom like "
                f"{example_text('100,300,200,550')}; {paint('zero is allowed inside inline crop', Color.ZERO_INLINE)}"
            )
        value = ask_raw(
            question_prompt(
                answers,
                "Apply crop?",
                crop_hint,
                "n",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["crop_enabled"] = False
            answers["crop_values_inline"] = False
            for key in ("crop_top", "crop_left", "crop_right", "crop_bottom"):
                answers.pop(key, None)
            return
        if lowered in {"y", "yes"}:
            answers["crop_enabled"] = True
            answers["crop_values_inline"] = False
            return
        if lowered in {"g", "gui", "preview"}:
            if not allow_gui:
                error("Graphical crop editor is not available in Folder Encode.")
                continue
            note("Loading Graphical Crop Editor...")
            sys.stdout.flush()
            margins = choose_crop_graphically(answers)
            if margins is None:
                if answers.pop("_last_gui_error", None) == "crop":
                    note("Graphical crop preview failed. Returning to the crop question.")
                else:
                    note("Graphical crop preview was canceled.")
                continue
            top, left, right, bottom = margins
            if not set_crop_margins_if_valid(answers, top, left, right, bottom):
                continue
            answers["crop_values_inline"] = True
            print(paint(f"Applied graphical crop: {format_crop_margins(answers)}", Color.LIME))
            return

        pieces = [piece.strip() for piece in value.split(",")]
        if len(pieces) != 4 or any(not re.fullmatch(r"\d+", piece) for piece in pieces):
            if allow_gui:
                error("Enter y, n, g, or four integer crop margins: top,left,right,bottom")
            else:
                error("Enter y, n, or four integer crop margins: top,left,right,bottom")
            continue
        top, left, right, bottom = [int(piece) for piece in pieces]
        if not set_crop_margins_if_valid(answers, top, left, right, bottom):
            continue
        answers["crop_values_inline"] = True
        return


def step_crop_top(answers: dict[str, Any]) -> None:
    while True:
        value = ask_positive_int_or_n(
            question_prompt(answers, "Enter crop top px", "integer pixels; use 00 for zero because 0=back"),
            allow_n=False,
            allow_zero_word=True,
        )
        if set_single_crop_margin_if_valid(answers, "crop_top", value):
            return


def step_crop_left(answers: dict[str, Any]) -> None:
    while True:
        value = ask_positive_int_or_n(
            question_prompt(answers, "Enter crop left px", "integer pixels; use 00 for zero because 0=back"),
            allow_n=False,
            allow_zero_word=True,
        )
        if set_single_crop_margin_if_valid(answers, "crop_left", value):
            return


def step_crop_right(answers: dict[str, Any]) -> None:
    while True:
        value = ask_positive_int_or_n(
            question_prompt(answers, "Enter crop right px", "integer pixels; use 00 for zero because 0=back"),
            allow_n=False,
            allow_zero_word=True,
        )
        if set_single_crop_margin_if_valid(answers, "crop_right", value):
            return


def step_crop_bottom(answers: dict[str, Any]) -> None:
    while True:
        value = ask_positive_int_or_n(
            question_prompt(answers, "Enter crop bottom px", "integer pixels; use 00 for zero because 0=back"),
            allow_n=False,
            allow_zero_word=True,
        )
        if set_single_crop_margin_if_valid(answers, "crop_bottom", value):
            return


def step_video_bitrate(answers: dict[str, Any]) -> None:
    packet_sizes = get_packet_sizes(answers)
    source = stream_bitrate_kbps(answers["video_streams"][0], answers.get("format"), packet_sizes)
    source_limit, source_limit_label = detected_video_bitrate_limit(answers)
    suggested = source or DEFAULT_OUTPUT_VIDEO_BITRATE_KBPS
    prompt = question_prompt(
        answers,
        "Enter average video bitrate in kbps",
        f"examples: {example_text('400,800,1500')}; "
        f"{keep_value_text('n=keep current value' + (' around ' + str(source) + 'k' if source else ''))}; "
        f"{suggestion_text(f'suggestion: {suggested}')}",
        str(suggested),
    )
    while True:
        value = ask_raw(prompt)
        if value == "0":
            raise Back()
        if not value:
            value = str(suggested)
        if value.lower() == "n":
            answers["video_bitrate_kbps"] = source
            answers["video_bitrate_keep"] = True
            return
        if not re.fullmatch(r"\d+", value):
            error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        if not confirm_numeric_target_not_above_source(
            answers,
            "video bitrate",
            number,
            source_limit,
            source_limit_label,
            "kbps",
            "higher video bitrate can increase file size without adding real source detail",
        ):
            continue
        answers["video_bitrate_kbps"] = number
        answers["video_bitrate_keep"] = False
        return


def step_resolution(answers: dict[str, Any]) -> None:
    while True:
        prompt = question_prompt(
            answers,
            "Enter output resolution",
            f"presets/plain numbers preserve aspect ratio using closest-edge scaling: {option_list(list(RESOLUTION_PRESETS))}, "
            f"{paint('numbers without p like 480', Color.RES_NUMBERS)}, "
            f"{paint('force width like w720', Color.RES_TARGET)}, "
            f"{paint('force height like h480', Color.RES_TARGET)}, "
            f"{paint('target box like', Color.RES_EXACT)} {example_text('1280x720')}, "
            f"{paint('exact stretch like', Color.RES_EXACT)} {example_text('stretch:1280x720')}; "
            +
            keep_value_text("n=current resolution"),
            "n",
        )
        value = ask_raw(prompt)
        if value == "0":
            raise Back()
        if not value:
            value = "n"
        try:
            parsed = parse_resolution(value)
        except ValueError as exc:
            error(str(exc))
            continue
        if not confirm_resolution_not_above_source(answers, parsed):
            continue
        answers["resolution"] = parsed
        return


def step_fps(answers: dict[str, Any]) -> None:
    fps = rational_to_float(answers["video_streams"][0].get("avg_frame_rate"))
    source_limit, source_limit_label = detected_fps_limit(answers)
    keep_fps_text = "n=current FPS" + (f" around {format(fps, '.3g')}" if fps else "")
    prompt = question_prompt(
        answers,
        "Enter frames per second",
        f"examples: {example_text('4,5,24,30,60')}; {keep_value_text(keep_fps_text)}",
        "n",
    )
    while True:
        value = ask_raw(prompt)
        if value == "0":
            raise Back()
        if not value:
            value = "n"
        if value.lower() == "n":
            answers["fps"] = None
            return
        if not re.fullmatch(r"\d+", value):
            error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        if not confirm_numeric_target_not_above_source(
            answers,
            "frames per second",
            number,
            source_limit,
            source_limit_label,
            "fps",
            "higher FPS duplicates or interpolates timing work without adding real captured frames",
        ):
            continue
        answers["fps"] = number
        return


def step_audio_tracks(answers: dict[str, Any]) -> None:
    streams = answers["audio_streams"]
    packet_sizes = get_packet_sizes(answers)
    report = detect_duplicate_audio(answers) if answers.get("detect_duplicate_audio", True) else None
    fmt = answers.get("format", {})
    print()
    print(paint("Detected audio tracks:", Color.BOLD + Color.BLUE))
    for idx, stream in enumerate(streams):
        size, _ = stream_size_bytes(stream, fmt, packet_sizes)
        labels = duplicate_labels(idx, report) if report else []
        label_text = f" | {' | '.join(labels)}" if labels else ""
        print(
            f"  {paint(stream_title(stream, idx), Color.WHITE)} | "
            f"{field_text('size', format_bytes(size), Color.LIME)}{label_text}"
        )

    prompt = question_prompt(
        answers,
        "Which audio tracks should be kept?",
        f"example: {example_text('0,1,2')}; {colored_audio_track_hint()}",
        "de",
        back="back=b, quit=exit",
    )
    while True:
        value = ask_raw(prompt)
        lowered = value.lower()
        if not value:
            answers["audio_tracks_mode"] = "de"
            answers["audio_tracks"] = auto_select_audio_tracks(answers, "de")
            print(paint(f"Auto-selected audio tracks: {answers['audio_tracks']}", Color.LIME))
            return
        if lowered in {"b", "back"}:
            raise Back()
        if lowered in {"d", "e", "de", "ed"}:
            answers["audio_tracks_mode"] = lowered
            answers["audio_tracks"] = auto_select_audio_tracks(answers, lowered)
            print(paint(f"Auto-selected audio tracks: {answers['audio_tracks']}", Color.LIME))
            return
        try:
            answers["audio_tracks"] = parse_selection_config(value, len(streams), [0])
            answers["audio_tracks_mode"] = "manual"
            return
        except ValueError as exc:
            error(str(exc))


def step_audio_codec(answers: dict[str, Any]) -> None:
    default_codec = default_audio_codec_for_ext(answers.get("output_ext", ""))
    value = ask_raw(
        question_prompt(
            answers,
            "Enter audio codec",
            f"common: {option_list(COMMON_AUDIO_CODECS)}; {keep_value_text('n=copy current audio stream without re-encoding')}",
            "n" if answers.get("_folder_encode_mode") else default_codec,
        )
    )
    if value == "0":
        raise Back()
    if not value:
        value = "n" if answers.get("_folder_encode_mode") else default_codec
    if value.lower() == "n":
        value = "copy"
    answers["audio_codec"] = value


def step_audio_bitrate(answers: dict[str, Any]) -> None:
    first_selected = selected_audio_streams(answers)[0]
    packet_sizes = get_packet_sizes(answers)
    source = stream_bitrate_kbps(answers["audio_streams"][first_selected], answers.get("format"), packet_sizes)
    source_limit, source_limit_label = detected_audio_bitrate_limit(answers)
    default_audio_bitrate = DEFAULT_AUDIO_BITRATE_KBPS
    if source and source < DEFAULT_AUDIO_BITRATE_KBPS:
        default_audio_bitrate = source
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Enter audio bitrate in kbps",
                f"examples: {example_text('64,96,128,160,192,256,320')}; "
                f"{keep_value_text('n=keep current value' + (' around ' + str(source) + 'k' if source else ''))}",
                str(default_audio_bitrate),
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = str(default_audio_bitrate)
        if value.lower() == "n":
            answers["audio_bitrate_kbps"] = source
            answers["audio_bitrate_keep"] = True
            return
        if not re.fullmatch(r"\d+", value):
            error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        if not confirm_numeric_target_not_above_source(
            answers,
            "audio bitrate",
            number,
            source_limit,
            source_limit_label,
            "kbps",
            "higher audio bitrate can increase file size without adding real source quality",
        ):
            continue
        answers["audio_bitrate_kbps"] = number
        answers["audio_bitrate_keep"] = False
        return


def step_subtitle_tracks(answers: dict[str, Any]) -> None:
    streams = answers["subtitle_streams"]
    print()
    print(paint(f"Detected {len(streams)} subtitle track(s):", Color.BOLD + Color.WHITE))
    for idx, stream in enumerate(streams):
        print("  " + paint(stream_title(stream, idx), Color.WHITE))
    answers["subtitle_tracks"] = ask_selection(
        question_prompt(
            answers,
            "Which subtitle tracks should be kept?",
            f"example: {example_text('0,1')}; Enter=track 0; n/all=all; none/clear=remove all; 0 is track 0 here",
            back="back=b, quit=exit",
        ),
        max_count=len(streams),
        default=[0],
        allow_none=True,
    )


def build_output_path(answers: dict[str, Any]) -> Path:
    input_path: Path = answers["input_path"]
    output_location: Path = answers["output_location"]
    output_ext = answers["output_ext"]

    if answers.get("output_name_stem"):
        output_path = output_location / f"{sanitize_output_stem(answers['output_name_stem'])}.{output_ext}"
    elif output_location.suffix:
        output_path = output_location.with_suffix("." + output_ext)
        output_path = output_path.with_name(f"{sanitize_output_stem(output_path.stem)}{output_path.suffix}")
    else:
        output_path = output_location / f"{sanitize_output_stem(input_path.stem)}.{output_ext}"

    collision_suffix = answers.get("output_collision_suffix", "_Encode")
    output_path = resolve_output_collision(output_path, input_path, collision_suffix)
    log_info(f"Resolved output path: {output_path}")
    return output_path


def resolve_video_encoder(answers: dict[str, Any]) -> tuple[str, str | None, str | None]:
    requested = answers.get("video_codec", DEFAULT_VIDEO_CODEC).strip()
    lowered = requested.lower()
    if lowered == "copy":
        return "copy", None, None

    info = VIDEO_CODEC_ALIASES.get(lowered)
    if info:
        if answers.get("use_gpu") and info.get("gpu"):
            return info["gpu"], info.get("tag"), info.get("profile")
        return info["cpu"], info.get("tag"), info.get("profile")

    return requested, None, None


def video_filters_required(answers: dict[str, Any]) -> bool:
    return bool(
        answers.get("crop_enabled")
        or answers.get("fps") is not None
        or answers.get("resolution", "n") != "n"
        or answers.get("cut_keep_ranges")
        or video_speed_transform_enabled(answers)
    )


def has_crop(answers: dict[str, Any]) -> bool:
    return bool(
        answers.get("crop_enabled")
        and any(int(answers.get(key, 0) or 0) for key in ("crop_top", "crop_bottom", "crop_left", "crop_right"))
    )


def crop_margins_to_cuvid_crop(answers: dict[str, Any]) -> str | None:
    if not has_crop(answers):
        return None
    top = int(answers.get("crop_top", 0) or 0)
    bottom = int(answers.get("crop_bottom", 0) or 0)
    left = int(answers.get("crop_left", 0) or 0)
    right = int(answers.get("crop_right", 0) or 0)
    return f"{top}x{bottom}x{left}x{right}"


def source_video_codec_name(answers: dict[str, Any]) -> str:
    streams = answers.get("video_streams") or []
    if not streams:
        return ""
    return str(streams[0].get("codec_name", "") or "").strip().lower()


def cuda_decoder_for_source(answers: dict[str, Any]) -> str | None:
    return CUDA_CUVID_DECODER_BY_CODEC.get(source_video_codec_name(answers))


def is_single_contiguous_cut(answers: dict[str, Any]) -> bool:
    return len(list(answers.get("cut_keep_ranges") or [])) == 1


def can_use_cuda_fast_path(answers: dict[str, Any], video_encoder: str | None) -> bool:
    cut_ranges = list(answers.get("cut_keep_ranges") or [])
    if has_crop(answers) and not cuda_decoder_for_source(answers):
        return False
    return bool(
        answers.get("use_gpu")
        and output_has_video(answers)
        and video_encoder
        and video_encoder != "copy"
        and str(video_encoder).endswith("_nvenc")
        and not answers.get("_hardsub_mode")
        and len(cut_ranges) <= 1
        and not answers.get("_force_cpu_video_filter")
        and not video_speed_transform_enabled(answers)
    )


def build_cuda_video_filter(answers: dict[str, Any]) -> str | None:
    resolution = answers.get("resolution", "n")
    scale_dimensions = resolve_scale_dimensions(answers, resolution)
    if scale_dimensions:
        width, height = scale_dimensions
        return (
            f"scale_cuda=w={width}:h={height}:format={CUDA_FORMAT}:"
            "interp_algo=bicubic:passthrough=0:reset_sar=1"
        )
    return f"scale_cuda=format={CUDA_FORMAT}:passthrough=0:reset_sar=1"


def build_cpu_video_filter(answers: dict[str, Any]) -> str | None:
    filters: list[str] = []
    if answers.get("crop_enabled"):
        left = answers["crop_left"]
        right = answers["crop_right"]
        top = answers["crop_top"]
        bottom = answers["crop_bottom"]
        filters.append(f"crop=iw-{left}-{right}:ih-{top}-{bottom}:{left}:{top}")

    if answers.get("fps") is not None:
        filters.append(f"fps={answers['fps']}")

    resolution = answers.get("resolution", "n")
    scale_dimensions = resolve_scale_dimensions(answers, resolution)
    if scale_dimensions:
        width, height = scale_dimensions
        filters.append(f"scale={width}:{height}")

    if video_speed_transform_enabled(answers):
        filters.append(build_video_speed_filter(encode_video_speed_factor(answers), bool(answers.get("reverse_video"))))

    if FORCE_SAR:
        filters.append(f"setsar={FORCE_SAR}")

    if SETPARAMS_RANGE:
        filters.append(f"setparams=range={SETPARAMS_RANGE}")

    filters.append(f"format={CPU_FORMAT}")
    return ",".join(filters) if filters else None


def build_cpu_fallback_from_cuda_filter(answers: dict[str, Any]) -> str | None:
    cpu_filter = build_cpu_video_filter(answers)
    if not cpu_filter:
        return None
    return f"hwdownload,format={CUDA_FORMAT},{cpu_filter},format={CUDA_FORMAT},hwupload_cuda"


def build_video_filter(answers: dict[str, Any], use_gpu_filtering: bool) -> str | None:
    return build_cuda_video_filter(answers) if use_gpu_filtering else build_cpu_video_filter(answers)


def build_audio_transform_filter_complex(
    answers: dict[str, Any],
    audio_indices: list[int],
) -> tuple[str, list[str]]:
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_ranges = normalize_cut_ranges(list(answers.get("audio_cut_keep_ranges") or []), duration)
    parts: list[str] = []
    output_labels: list[str] = []
    for pos, audio_index in enumerate(audio_indices):
        current_label = f"0:a:{audio_index}"
        if keep_ranges:
            range_labels: list[str] = []
            for range_idx, (start, end) in enumerate(keep_ranges):
                label = f"acut{pos}_{range_idx}"
                range_labels.append(f"[{label}]")
                parts.append(
                    f"[{current_label}]atrim=start={start:.6f}:end={end:.6f},"
                    f"asetpts=PTS-STARTPTS[{label}]"
                )
            cut_label = f"acut{pos}"
            parts.append(f"{''.join(range_labels)}concat=n={len(keep_ranges)}:v=0:a=1[{cut_label}]")
            current_label = cut_label
        out_label = f"aout{pos}"
        if audio_speed_transform_enabled(answers):
            parts.append(f"[{current_label}]{build_encode_audio_speed_filter(answers)}[{out_label}]")
        elif current_label.startswith("0:"):
            parts.append(f"[{current_label}]anull[{out_label}]")
        else:
            parts.append(f"[{current_label}]asetpts=PTS-STARTPTS[{out_label}]")
        output_labels.append(out_label)
    return ";".join(parts), output_labels


def video_bitrate_mode(answers: dict[str, Any]) -> str:
    mode = str(answers.get("video_bitrate_mode") or "quality_vbr").strip().lower()
    return mode if mode in {"quality_vbr", "strict_size"} else "quality_vbr"


def append_video_bitrate_args(cmd: list[str], answers: dict[str, Any], bitrate_kbps: int) -> None:
    mode = video_bitrate_mode(answers)
    if mode == "strict_size":
        maxrate = bitrate_kbps
        bufsize = bitrate_kbps * 2
    else:
        maxrate = bitrate_kbps * 2
        bufsize = bitrate_kbps * 4
    cmd.extend(["-b:v", f"{bitrate_kbps}k", "-maxrate:v", f"{maxrate}k", "-bufsize:v", f"{bufsize}k"])


def ps_quote(arg: str) -> str:
    if arg == "":
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_./:+=-]+", arg):
        return arg
    return "'" + arg.replace("'", "''") + "'"


def command_to_powershell(args: list[str]) -> str:
    return " ".join(ps_quote(arg) for arg in args)


def build_ffmpeg_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    output_path = build_output_path(answers)
    answers["output_path"] = output_path
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"Could not create output folder: {output_path.parent}. {exc}") from exc

    # FFmpeg command rules used here:
    # - Explicit -map options disable automatic stream selection for this output.
    # - Streamcopy (-c copy) skips decoding/filtering/encoding and cannot be used
    #   for a stream that needs crop/fps/scale/color-parameter filters.
    # - Audio-only outputs disable video/subtitle streams with -vn and -sn.
    # - MP4-like outputs get mov_text only for text subtitles, plus faststart/tag
    #   options that are valid for that family of muxers.
    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n"]

    has_video = output_has_video(answers)
    video_encoder = None
    tag = None
    profile = None
    use_cuda_fast_path = False

    cut_keep_ranges = list(answers.get("cut_keep_ranges") or [])
    cut_active = bool(cut_keep_ranges) and has_video
    single_cut = len(cut_keep_ranges) == 1 and has_video
    multi_cut = len(cut_keep_ranges) > 1 and has_video

    if has_video:
        if answers.get("output_ext", "").lower() == "webm" and str(answers.get("video_codec", "")).lower() in {
            "h264",
            "h265",
            "hevc",
            "mpeg4",
        }:
            note("WebM does not support that video codec safely here. VP9 was selected for this output.")
            answers["video_codec"] = "VP9"
        video_encoder, tag, profile = resolve_video_encoder(answers)
        if video_encoder == "copy" and video_filters_required(answers):
            note(
                "\nWarning: video copy cannot be used with crop/fps/scale/setparams or cuts. "
                "H265 was selected so filters/cuts can be applied."
            )
            answers["video_codec"] = DEFAULT_VIDEO_CODEC
            video_encoder, tag, profile = resolve_video_encoder(answers)

        use_cuda_fast_path = can_use_cuda_fast_path(answers, video_encoder)
        if answers.get("use_gpu") and video_encoder != "copy" and not str(video_encoder).endswith("_nvenc"):
            note("The selected video encoder is not NVENC, so CPU decode/filter/encode will be used for video.")
        elif multi_cut and answers.get("use_gpu") and str(video_encoder).endswith("_nvenc"):
            log_info("Multiple cut ranges use CPU trim/concat filter_complex; NVENC encode remains enabled.")
        elif (
            has_crop(answers)
            and answers.get("use_gpu")
            and video_encoder != "copy"
            and str(video_encoder).endswith("_nvenc")
            and not use_cuda_fast_path
        ):
            codec_name = source_video_codec_name(answers) or "unknown"
            log_info(
                "CUDA decoder crop is unavailable for source codec "
                f"{codec_name}; using CPU crop filter before NVENC encode to avoid stretch."
            )

        if use_cuda_fast_path:
            cmd.extend(["-hwaccel", "cuda", "-hwaccel_device", str(GPU_DEVICE_INDEX), "-hwaccel_output_format", "cuda"])
            cuvid_crop = crop_margins_to_cuvid_crop(answers)
            if cuvid_crop:
                cuda_decoder = cuda_decoder_for_source(answers)
                if cuda_decoder:
                    cmd.extend(["-c:v", cuda_decoder])
                cmd.extend(["-crop", cuvid_crop])

    if single_cut:
        start, end = cut_keep_ranges[0]
        if start > 0:
            cmd.extend(["-ss", f"{start:.6f}"])

    cmd.extend(["-i", str(input_path)])
    if single_cut:
        start, end = cut_keep_ranges[0]
        cmd.extend(["-t", f"{max(0.0, end - start):.6f}"])

    # Determine audio mapping. When multi-range cuts are active, only one audio
    # output stream is produced by the filter_complex concat. Pick the first
    # selected audio in that path.
    if answers.get("audio_speed_from_video") and answers.get("audio_streams"):
        audio_indices = list(range(len(answers.get("audio_streams") or [])))
    else:
        audio_indices = selected_audio_streams(answers) if answers.get("audio_streams") else []
    audio_transform_active = bool(audio_indices) and audio_transform_enabled(answers)
    if multi_cut and audio_cut_transform_enabled(answers):
        note("Audio waveform cuts are skipped when video multi-range cuts are active.")
        audio_transform_active = audio_speed_transform_enabled(answers)
    audio_for_cut: int | None = None
    if multi_cut and audio_indices:
        audio_for_cut = audio_indices[0]
        if len(audio_indices) > 1:
            note(
                "Cuts active: only the first selected audio track survives the "
                f"filter_complex concat. Using stream index 0:a:{audio_for_cut}."
            )
        audio_indices = [audio_for_cut]

    if has_video:
        if multi_cut:
            cmd.extend(["-map", "[v]"])
        else:
            cmd.extend(["-map", "0:v:0"])

    if multi_cut and audio_for_cut is not None:
        cmd.extend(["-map", "[a]"])
    elif audio_transform_active and not multi_cut:
        pass
    elif not multi_cut:
        for audio_index in audio_indices:
            cmd.extend(["-map", f"0:a:{audio_index}"])

    subtitle_indices = selected_subtitle_streams(answers) if has_video and answers.get("subtitle_streams") else []
    if multi_cut and subtitle_indices:
        note("Cuts active: subtitle streams are not mapped through filter_complex and were skipped.")
        subtitle_indices = []
    if subtitle_indices and answers["output_ext"].lower() in MP4_LIKE_EXTS:
        allowed_subtitles: list[int] = []
        skipped_subtitles: list[int] = []
        for subtitle_index in subtitle_indices:
            codec = str(answers["subtitle_streams"][subtitle_index].get("codec_name", "")).lower()
            if codec in TEXT_SUBTITLE_CODECS:
                allowed_subtitles.append(subtitle_index)
            else:
                skipped_subtitles.append(subtitle_index)
        if skipped_subtitles:
            note(f"Skipped non-text subtitle tracks for MP4/MOV output: {skipped_subtitles}")
        subtitle_indices = allowed_subtitles
    for subtitle_index in subtitle_indices:
        cmd.extend(["-map", f"0:s:{subtitle_index}"])

    if not has_video:
        cmd.append("-vn")
    if not subtitle_indices:
        cmd.append("-sn")
    cmd.append("-dn")

    if has_video and video_encoder:
        video_bitrate = answers.get("video_bitrate_kbps")
        if video_encoder == "copy":
            cmd.extend(["-c:v", "copy"])
        else:
            if multi_cut:
                fc = build_cut_filter_complex(answers, cut_keep_ranges, audio_for_cut)
                cmd.extend(["-filter_complex", fc])
            else:
                video_filter = build_video_filter(answers, use_gpu_filtering=use_cuda_fast_path)
                if video_filter:
                    cmd.extend(["-filter:v", video_filter])
            if use_cuda_fast_path and answers.get("fps") is not None:
                cmd.extend(["-r:v", str(answers["fps"]), "-fps_mode:v", "cfr"])
            cmd.extend(["-c:v", video_encoder])

            if video_encoder.endswith("_nvenc"):
                cmd.extend(["-preset", NVENC_PRESET, "-tune", NVENC_TUNE, "-rc", NVENC_RC])
                if profile and "hevc" in video_encoder:
                    cmd.extend(["-profile:v", profile])
            elif video_encoder in {"libx264", "libx265"}:
                cmd.extend(["-preset", CPU_PRESET])

            if video_bitrate:
                append_video_bitrate_args(cmd, answers, int(video_bitrate))

            cmd.extend(["-color_range", COLOR_RANGE])

            if tag and answers["output_ext"].lower() in MP4_LIKE_EXTS:
                cmd.extend(["-tag:v", tag])

    if audio_indices:
        if audio_transform_active and not multi_cut:
            audio_fc, audio_labels = build_audio_transform_filter_complex(answers, audio_indices)
            cmd.extend(["-filter_complex", audio_fc])
            for label in audio_labels:
                cmd.extend(["-map", f"[{label}]"])
        audio_codec = answers.get("audio_codec") or default_audio_codec_for_ext(answers.get("output_ext", ""))
        if audio_transform_active and audio_codec.lower() == "copy":
            note("Audio copy cannot be used with audio speed/reverse or waveform cuts. AAC was selected for audio.")
            audio_codec = DEFAULT_AUDIO_CODEC
            answers["audio_codec"] = audio_codec
        if answers.get("output_ext", "").lower() == "webm" and audio_codec.lower() not in {"copy", "libopus", "libvorbis"}:
            note("WebM audio was changed to libopus for container compatibility.")
            audio_codec = "libopus"
            answers["audio_codec"] = audio_codec
        if audio_codec.lower() == "copy":
            cmd.extend(["-c:a", "copy"])
        else:
            if audio_codec.lower() == "aac":
                log_info("AAC audio encoding is CPU-side; video CUDA/NVENC path is unaffected.")
            cmd.extend(["-c:a", audio_codec])
            audio_bitrate = answers.get("audio_bitrate_kbps")
            if audio_bitrate and audio_codec_uses_bitrate(audio_codec):
                cmd.extend(["-b:a", f"{audio_bitrate}k"])
            if AUDIO_CHANNELS:
                cmd.extend(["-ac", str(AUDIO_CHANNELS)])
            if AUDIO_SAMPLE_RATE:
                cmd.extend(["-ar", str(AUDIO_SAMPLE_RATE)])
    else:
        cmd.append("-an")

    if subtitle_indices:
        if answers["output_ext"].lower() in MP4_LIKE_EXTS:
            cmd.extend(["-c:s", "mov_text"])
        else:
            cmd.extend(["-c:s", "copy"])

    if answers["output_ext"].lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])

    cmd.append(str(output_path))
    return cmd


def build_video_speed_reverse_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    answers["output_collision_suffix"] = speed_suffix(
        float(answers.get("speed_factor", DEFAULT_SPEED_FACTOR)),
        bool(answers.get("reverse_video")),
    )
    output_path = build_output_path(answers)
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    speed = clamp_speed_factor(answers.get("speed_factor", DEFAULT_SPEED_FACTOR))
    reverse = bool(answers.get("reverse_video"))
    include_audio = bool(answers.get("include_audio", True)) and bool(answers.get("audio_streams"))
    audio_count = len(answers.get("audio_streams") or [])

    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n", "-i", str(input_path)]
    cmd.extend(["-map", "0:v:0"])
    cmd.extend(["-sn", "-dn"])
    cmd.extend(["-filter:v", build_video_speed_filter(speed, reverse)])
    cmd.extend(["-c:v", "libx264", "-preset", CPU_PRESET, "-crf", "18", "-pix_fmt", CPU_FORMAT])
    if include_audio:
        labels: list[str] = []
        parts: list[str] = []
        for index in range(audio_count):
            label = f"aspd{index}"
            labels.append(label)
            parts.append(f"[0:a:{index}]{build_audio_speed_filter(speed, reverse)}[{label}]")
        cmd.extend(["-filter_complex", ";".join(parts)])
        for label in labels:
            cmd.extend(["-map", f"[{label}]"])
        cmd.extend(["-c:a", DEFAULT_AUDIO_CODEC, "-b:a", f"{DEFAULT_SPEED_AUDIO_BITRATE_KBPS}k"])
        if AUDIO_CHANNELS:
            cmd.extend(["-ac", str(AUDIO_CHANNELS)])
    else:
        cmd.append("-an")
    if answers["output_ext"].lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])
    cmd.append(str(output_path))
    log_info(
        f"Video speed/reverse command built: speed={speed}; reverse={reverse}; "
        f"include_audio={include_audio}; output={output_path}"
    )
    return cmd


def build_video_speed_reverse_segment_command(
    answers: dict[str, Any],
    start: float,
    end: float,
    output_path: Path,
) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    speed = clamp_speed_factor(answers.get("speed_factor", DEFAULT_SPEED_FACTOR))
    reverse = bool(answers.get("reverse_video"))
    include_audio = bool(answers.get("include_audio", True)) and bool(answers.get("audio_streams"))
    audio_count = len(answers.get("audio_streams") or [])
    cmd: list[str] = [
        ffmpeg,
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-ss",
        ffmpeg_float(start),
        "-t",
        ffmpeg_float(max(0.0, end - start)),
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
    ]
    cmd.extend(["-sn", "-dn", "-filter:v", build_video_speed_filter(speed, reverse)])
    cmd.extend(["-c:v", "libx264", "-preset", CPU_PRESET, "-crf", "18", "-pix_fmt", CPU_FORMAT])
    if include_audio:
        labels: list[str] = []
        parts: list[str] = []
        for index in range(audio_count):
            label = f"aspd{index}"
            labels.append(label)
            parts.append(f"[0:a:{index}]{build_audio_speed_filter(speed, reverse)}[{label}]")
        cmd.extend(["-filter_complex", ";".join(parts)])
        for label in labels:
            cmd.extend(["-map", f"[{label}]"])
        cmd.extend(["-c:a", DEFAULT_AUDIO_CODEC, "-b:a", f"{DEFAULT_SPEED_AUDIO_BITRATE_KBPS}k"])
        if AUDIO_CHANNELS:
            cmd.extend(["-ac", str(AUDIO_CHANNELS)])
    else:
        cmd.append("-an")
    if output_path.suffix.lstrip(".").lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])
    cmd.append(str(output_path))
    return cmd


def build_concat_copy_command(ffmpeg: str, concat_list: Path, output_path: Path) -> list[str]:
    cmd = [
        ffmpeg,
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-hide_banner",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-map",
        "0",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
    ]
    if output_path.suffix.lstrip(".").lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])
    cmd.append(str(output_path))
    return cmd


def run_segmented_reverse_video_speed(answers: dict[str, Any]) -> tuple[int, float]:
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    if duration <= 0:
        return run_ffmpeg_with_progress(
            answers["cmd"],
            total_duration=None,
            label="Video Speed / Reverse",
        )
    output_path = Path(answers["output_path"])
    speed = clamp_speed_factor(answers.get("speed_factor", DEFAULT_SPEED_FACTOR))
    chunks = split_ranges_for_reverse_segments([], duration)
    if not chunks:
        return 1, 0.0
    note(
        f"Reverse mode uses {len(chunks)} segment(s) of up to {int(REVERSE_SEGMENT_SECONDS)}s "
        "to avoid buffering the full video in RAM."
    )
    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ffmwiz_reverse_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        segment_paths: list[Path] = []
        segment_ext = output_path.suffix.lstrip(".") or "mp4"
        for idx, (start, end) in enumerate(chunks, start=1):
            segment_path = tmpdir / f"reverse_seg_{idx:04d}.{segment_ext}"
            segment_paths.append(segment_path)
            cmd = build_video_speed_reverse_segment_command(answers, start, end, segment_path)
            log_info(f"Reverse segment {idx}/{len(chunks)} command: {command_to_powershell(cmd)}")
            note(f"Reverse segment {idx}/{len(chunks)}: {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}")
            rc, _ = run_ffmpeg_with_progress(
                cmd,
                total_duration=max(0.001, (end - start) / speed),
                label=f"Reverse segment {idx}/{len(chunks)}",
            )
            if rc != 0:
                return rc, time.perf_counter() - started_at
        concat_list = tmpdir / "concat.txt"
        write_concat_list(list(reversed(segment_paths)), concat_list)
        concat_cmd = build_concat_copy_command(answers["ffmpeg"], concat_list, output_path)
        log_info("Reverse concat command: " + command_to_powershell(concat_cmd))
        note("Concatenating reversed segments...")
        rc, _ = run_ffmpeg_with_progress(
            concat_cmd,
            total_duration=(duration / speed if duration > 0 else None),
            label="Reverse concat",
        )
        return rc, time.perf_counter() - started_at


def reverse_video_needs_segmented_main_encode(answers: dict[str, Any]) -> bool:
    return bool(output_has_video(answers) and video_speed_transform_enabled(answers) and answers.get("reverse_video"))


def build_main_encode_reverse_segment_command(
    answers: dict[str, Any],
    start: float,
    end: float,
    output_path: Path,
) -> list[str]:
    segment_answers = dict(answers)
    segment_answers["cut_keep_ranges"] = [(start, end)]
    segment_answers["output_location"] = output_path.parent
    segment_answers["output_name_stem"] = output_path.stem
    segment_answers["output_ext"] = output_path.suffix.lstrip(".") or str(answers.get("output_ext") or "mp4")
    segment_answers["output_collision_suffix"] = ""
    segment_answers.pop("output_path", None)
    return build_ffmpeg_command(segment_answers)


def run_segmented_reverse_main_encode(answers: dict[str, Any]) -> tuple[int, float]:
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    if duration <= 0:
        return run_ffmpeg_with_progress(
            answers["cmd"],
            total_duration=None,
            label="FFmpeg encode",
        )
    original_keep_ranges = normalize_cut_ranges(list(answers.get("cut_keep_ranges") or []), duration)
    chunks = split_ranges_for_reverse_segments(original_keep_ranges, duration)
    if not chunks:
        return 1, 0.0
    speed = encode_video_speed_factor(answers)
    output_path = Path(answers["output_path"])
    note(
        f"Reverse encode uses {len(chunks)} segment(s) of up to {int(REVERSE_SEGMENT_SECONDS)}s "
        "to avoid buffering the full video in RAM."
    )
    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ffmwiz_reverse_encode_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        segment_ext = output_path.suffix.lstrip(".") or str(answers.get("output_ext") or "mp4")
        segment_paths: list[Path] = []
        for idx, (start, end) in enumerate(chunks, start=1):
            segment_path = tmpdir / f"reverse_encode_seg_{idx:04d}.{segment_ext}"
            segment_paths.append(segment_path)
            cmd = build_main_encode_reverse_segment_command(answers, start, end, segment_path)
            log_info(f"Reverse encode segment {idx}/{len(chunks)} command: {command_to_powershell(cmd)}")
            note(f"Reverse encode segment {idx}/{len(chunks)}: {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}")
            rc, _ = run_ffmpeg_with_progress(
                cmd,
                total_duration=max(0.001, (end - start) / speed),
                label=f"Reverse encode segment {idx}/{len(chunks)}",
            )
            if rc != 0:
                return rc, time.perf_counter() - started_at
        concat_list = tmpdir / "concat.txt"
        write_concat_list(list(reversed(segment_paths)), concat_list)
        concat_cmd = build_concat_copy_command(answers["ffmpeg"], concat_list, output_path)
        log_info("Reverse encode concat command: " + command_to_powershell(concat_cmd))
        note("Concatenating reversed encoded segments...")
        rc, _ = run_ffmpeg_with_progress(
            concat_cmd,
            total_duration=(total_keep_duration(chunks) / speed if chunks else None),
            label="Reverse encode concat",
        )
        return rc, time.perf_counter() - started_at


def build_audio_speed_reverse_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    answers["output_ext"] = resolve_audio_tool_output_ext(answers)
    answers["output_collision_suffix"] = speed_suffix(
        float(answers.get("speed_factor", DEFAULT_SPEED_FACTOR)),
        bool(answers.get("reverse_audio")),
    )
    output_path = build_output_path(answers)
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    audio_index = int(answers.get("audio_index", 0))
    speed = clamp_speed_factor(answers.get("speed_factor", DEFAULT_SPEED_FACTOR))
    reverse = bool(answers.get("reverse_audio"))
    cmd: list[str] = [
        ffmpeg,
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-i",
        str(input_path),
        "-map",
        f"0:a:{audio_index}",
        "-vn",
        "-sn",
        "-dn",
        "-filter:a",
        build_audio_speed_filter(speed, reverse),
    ]
    cmd.extend(audio_tool_encode_options(answers["output_ext"]))
    cmd.append(str(output_path))
    log_info(
        f"Audio speed/reverse command built: audio_index={audio_index}; "
        f"speed={speed}; reverse={reverse}; output={output_path}"
    )
    return cmd


def build_audio_cut_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    answers["output_ext"] = resolve_audio_tool_output_ext(answers)
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_ranges = normalize_cut_ranges(list(answers.get("audio_keep_ranges") or []), duration)
    if not keep_ranges:
        raise ValueError("No valid audio keep ranges were selected.")
    answers["audio_keep_ranges"] = keep_ranges
    answers["output_collision_suffix"] = "_AudioCut"
    output_path = build_output_path(answers)
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    audio_index = int(answers.get("audio_index", 0))

    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n"]
    if len(keep_ranges) == 1:
        start, end = keep_ranges[0]
        if start > 0:
            cmd.extend(["-ss", f"{start:.6f}"])
        cmd.extend(["-i", str(input_path), "-t", f"{max(0.0, end - start):.6f}"])
        cmd.extend(["-map", f"0:a:{audio_index}", "-vn", "-sn", "-dn"])
    else:
        cmd.extend(["-i", str(input_path)])
        parts: list[str] = []
        labels: list[str] = []
        for idx, (start, end) in enumerate(keep_ranges):
            label = f"a{idx}"
            labels.append(f"[{label}]")
            parts.append(
                f"[0:a:{audio_index}]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS[{label}]"
            )
        parts.append(f"{''.join(labels)}concat=n={len(keep_ranges)}:v=0:a=1[a]")
        cmd.extend(["-filter_complex", ";".join(parts), "-map", "[a]", "-vn", "-sn", "-dn"])

    cmd.extend(audio_tool_encode_options(answers["output_ext"]))
    cmd.append(str(output_path))
    log_info(
        f"Audio cut command built: audio_index={audio_index}; ranges={keep_ranges}; output={output_path}"
    )
    return cmd


def build_audio_transform_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    answers["output_ext"] = resolve_audio_tool_output_ext(answers)
    suffix_parts: list[str] = []
    if answers.get("audio_cut_keep_ranges"):
        suffix_parts.append("AudioCut")
    if answers.get("audio_speed_enabled"):
        suffix_parts.append(
            speed_suffix(
                float(answers.get("audio_speed_factor", DEFAULT_SPEED_FACTOR)),
                bool(answers.get("reverse_audio")),
            ).lstrip("_")
        )
    answers["output_collision_suffix"] = "_" + "_".join(suffix_parts or ["AudioTransform"])
    output_path = build_output_path(answers)
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    audio_index = int(answers.get("audio_index", 0))
    filter_complex, labels = build_audio_transform_filter_complex(answers, [audio_index])
    if not labels:
        raise ValueError("No audio stream was selected for transformation.")
    cmd: list[str] = [
        ffmpeg,
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-i",
        str(input_path),
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{labels[0]}]",
        "-vn",
        "-sn",
        "-dn",
    ]
    cmd.extend(audio_tool_encode_options(answers["output_ext"]))
    cmd.append(str(output_path))
    log_info(
        f"Audio transform command built: audio_index={audio_index}; "
        f"cuts={answers.get('audio_cut_keep_ranges')}; "
        f"speed={answers.get('audio_speed_factor')}; reverse={answers.get('reverse_audio')}; "
        f"output={output_path}"
    )
    return cmd


def step_start_now(answers: dict[str, Any]) -> None:
    if output_is_audio_only(answers) and not answers.get("audio_streams"):
        fail("Audio-only output was selected, but the input file has no audio stream.")

    cmd = build_ffmpeg_command(answers)
    answers["cmd"] = cmd
    print_summary(answers, cmd)
    answers["start_now"] = ask_yes_no(
        question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def parse_crop_config_value(answers: dict[str, Any], value: str, config: dict[str, Any]) -> None:
    lowered = value.lower().strip()
    if not lowered or lowered in {"n", "no", "false", "0", "off"}:
        answers["crop_enabled"] = False
        answers["crop_values_inline"] = False
        for key in ("crop_top", "crop_left", "crop_right", "crop_bottom"):
            answers.pop(key, None)
        return

    if lowered in {"y", "yes", "true", "1", "on"}:
        top = int(parse_int_config(config_value(config, "crop_top"), 0, allow_n=False) or 0)
        left = int(parse_int_config(config_value(config, "crop_left"), 0, allow_n=False) or 0)
        right = int(parse_int_config(config_value(config, "crop_right"), 0, allow_n=False) or 0)
        bottom = int(parse_int_config(config_value(config, "crop_bottom"), 0, allow_n=False) or 0)
    else:
        pieces = [piece.strip() for piece in value.split(",")]
        if len(pieces) != 4 or any(not re.fullmatch(r"\d+", piece) for piece in pieces):
            raise ValueError("crop must be n, y, or top,left,right,bottom")
        top, left, right, bottom = [int(piece) for piece in pieces]

    message = crop_margins_validation_message(answers, top, left, right, bottom)
    if message:
        raise ValueError(message)
    answers["crop_enabled"] = any((top, left, right, bottom))
    answers["crop_values_inline"] = True
    answers["crop_top"] = top
    answers["crop_left"] = left
    answers["crop_right"] = right
    answers["crop_bottom"] = bottom


def apply_config_video_options(
    answers: dict[str, Any],
    config: dict[str, Any],
    skip_crop: bool = False,
    force_video_options: bool = False,
) -> None:
    video_codec = config_value(config, "video_codec") or DEFAULT_VIDEO_CODEC
    if video_codec.lower() == "n":
        video_codec = "copy"
    answers["video_codec"] = video_codec
    answers["use_gpu"] = parse_bool_config(config_value(config, "use_gpu"), True)
    if skip_crop:
        answers["crop_enabled"] = False
        answers["crop_values_inline"] = False
        for key in ("crop_top", "crop_left", "crop_right", "crop_bottom"):
            answers.pop(key, None)
    else:
        parse_crop_config_value(answers, config_value(config, "crop"), config)

    if not force_video_options and not video_reencode_options_applicable(answers):
        return

    packet_sizes = get_packet_sizes(answers)
    source_video_bitrate = stream_bitrate_kbps(answers["video_streams"][0], answers.get("format"), packet_sizes)
    video_bitrate_value = parse_int_config(
        config_value(config, "video_bitrate_kbps"),
        DEFAULT_OUTPUT_VIDEO_BITRATE_KBPS,
        allow_n=True,
    )
    if video_bitrate_value == "n":
        answers["video_bitrate_kbps"] = source_video_bitrate
        answers["video_bitrate_keep"] = True
    else:
        answers["video_bitrate_kbps"] = video_bitrate_value
        answers["video_bitrate_keep"] = False
    mode = str(config_value(config, "video_bitrate_mode") or "quality_vbr").strip().lower()
    answers["video_bitrate_mode"] = mode if mode in {"quality_vbr", "strict_size"} else "quality_vbr"

    resolution_value = config_value(config, "resolution") or "n"
    answers["resolution"] = parse_resolution(resolution_value)

    fps_value = parse_int_config(config_value(config, "fps"), None, allow_n=True)
    answers["fps"] = None if fps_value in {None, "n"} else fps_value


def apply_config_audio_options(answers: dict[str, Any], config: dict[str, Any]) -> None:
    if not answers.get("audio_streams"):
        return

    audio_tracks_value = config_value(config, "audio_tracks") or "de"
    if audio_tracks_value.lower() in {"d", "e", "de", "ed"}:
        answers["audio_tracks"] = auto_select_audio_tracks(answers, audio_tracks_value)
    else:
        answers["audio_tracks"] = parse_selection_config(
            audio_tracks_value,
            len(answers["audio_streams"]),
            [0],
        )
    if not selected_audio_streams(answers):
        return

    audio_codec = config_value(config, "audio_codec") or default_audio_codec_for_ext(answers.get("output_ext", ""))
    if audio_codec.lower() == "n":
        audio_codec = "copy"
    answers["audio_codec"] = audio_codec
    if audio_codec.lower() == "copy":
        return

    first_selected = selected_audio_streams(answers)[0]
    packet_sizes = get_packet_sizes(answers)
    source_audio_bitrate = stream_bitrate_kbps(answers["audio_streams"][first_selected], answers.get("format"), packet_sizes)
    audio_bitrate_value = parse_int_config(
        config_value(config, "audio_bitrate_kbps"),
        DEFAULT_AUDIO_BITRATE_KBPS,
        allow_n=True,
    )
    if audio_bitrate_value == "n":
        answers["audio_bitrate_kbps"] = source_audio_bitrate
        answers["audio_bitrate_keep"] = True
    else:
        answers["audio_bitrate_kbps"] = audio_bitrate_value
        answers["audio_bitrate_keep"] = False


def apply_config_subtitle_options(answers: dict[str, Any], config: dict[str, Any]) -> None:
    if not output_has_video(answers) or not answers.get("subtitle_streams"):
        return
    answers["subtitle_tracks"] = parse_selection_config(
        config_value(config, "subtitle_tracks"),
        len(answers["subtitle_streams"]),
        [0],
        allow_none=True,
    )


def load_answers_from_config(answers: dict[str, Any], path: Path, skip_crop: bool = False) -> None:
    ensure_config_file(path)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON config file: {path}. {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("settings"), dict):
        raise ValueError(f"Invalid config file: {path}. Expected a JSON object with a settings object.")

    input_value = config_value(config, "input_path")
    if not input_value:
        raise ValueError(f"input_path is required in config file: {path}")
    input_path = terminal_path(input_value)
    if not input_path.exists() or not input_path.is_file():
        raise ValueError(f"input_path does not exist: {input_path}")

    load_input_metadata(answers, input_path)
    answers["detect_duplicate_audio"] = parse_bool_config(config_value(config, "detect_duplicate_audio"), True)
    apply_output_location_value(answers, config_value(config, "output_path"))
    print_source_info(answers)

    input_ext = input_path.suffix.lstrip(".") or "mp4"
    default_ext = "mp4" if answers.get("video_streams") else "mp3"
    output_format = config_value(config, "output_format") or default_ext
    answers["output_ext"] = normalize_format(output_format, input_ext)

    if output_has_video(answers):
        apply_config_video_options(answers, config, skip_crop=skip_crop, force_video_options=skip_crop)

    apply_config_audio_options(answers, config)
    apply_config_subtitle_options(answers, config)


def run_wizard(answers: dict[str, Any]) -> None:
    steps = [
        Step("input_path", lambda a: True, step_input_path),
        Step("output_location", lambda a: True, step_output_location),
        Step("output_format", lambda a: True, step_output_format),
        Step("video_codec", output_has_video, step_video_codec),
        Step("use_gpu", output_has_video, step_use_gpu),
        Step("unified_video_editor", output_has_video, step_unified_video_editor_for_encode),
        Step("crop_enabled", output_has_video, step_crop_enabled),
        Step("crop_top", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_top),
        Step("crop_left", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_left),
        Step("crop_right", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_right),
        Step("crop_bottom", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_bottom),
        Step("video_bitrate", video_reencode_options_applicable, step_video_bitrate),
        Step("resolution", video_reencode_options_applicable, step_resolution),
        Step("fps", video_reencode_options_applicable, step_fps),
        Step("video_speed_reverse", output_has_video, step_video_speed_reverse_for_encode),
        Step("cuts", lambda a: video_reencode_options_applicable(a), step_cuts),
        Step("audio_tracks", lambda a: bool(a.get("audio_streams")), step_audio_tracks),
        Step("audio_cut", audio_only_transform_prompt_applicable, step_audio_cut_for_encode),
        Step("audio_speed_reverse", audio_only_transform_prompt_applicable, step_audio_speed_reverse_for_encode),
        Step("audio_codec", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_audio_codec),
        Step("audio_bitrate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy" and audio_codec_uses_bitrate(str(a.get("audio_codec") or default_audio_codec_for_ext(a.get("output_ext", "")))), step_audio_bitrate),
        Step("subtitle_tracks", lambda a: output_has_video(a) and bool(a.get("subtitle_streams")), step_subtitle_tracks),
        Step("start_now", lambda a: True, step_start_now),
    ]

    def next_index(start: int) -> int:
        idx = start
        while idx < len(steps) and not steps[idx].applicable(answers):
            idx += 1
        return idx

    def prev_index(start: int) -> int:
        idx = start
        while idx > 0 and not steps[idx].applicable(answers):
            idx -= 1
        return max(0, idx)

    def question_number(current: int) -> int:
        count = 0
        for pos in range(current + 1):
            if steps[pos].applicable(answers):
                count += 1
        return answers.get("_question_offset", 0) + count

    idx = next_index(0)
    while idx < len(steps):
        try:
            answers["_question_number"] = question_number(idx)
            steps[idx].run(answers)
            idx = next_index(idx + 1)
        except Back:
            if idx == 0:
                raise
            idx = prev_index(idx - 1)


def print_summary(answers: dict[str, Any], cmd: list[str]) -> None:
    log_info("Final PowerShell command: " + command_to_powershell(cmd))
    log_info(
        "Selected settings: input={}; output={}; format={}; video_codec={}; audio_codec={}; crop={}; fps={}; resolution={}".format(
            answers.get("input_path"), answers.get("output_path"), answers.get("output_ext"),
            answers.get("video_codec"), answers.get("audio_codec"),
            format_crop_margins(answers) if answers.get("crop_enabled") else "no",
            answers.get("fps") or "source", format_resolution_summary(answers.get("resolution")),
        )
    )
    print()
    print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    print()
    print(paint("Selected settings summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("input", answers["input_path"], Color.WHITE))
    print("  " + field_text("output", answers["output_path"], Color.LIME))
    if output_has_video(answers):
        print("  " + field_text("video codec", answers.get("video_codec", DEFAULT_VIDEO_CODEC), Color.CYAN))
        print("  " + field_text("GPU", "yes" if answers.get("use_gpu") else "no", Color.GREEN if answers.get("use_gpu") else Color.YELLOW))
        if answers.get("crop_enabled"):
            print("  " + field_text("crop", "yes, " + format_crop_margins(answers), Color.ORANGE))
            crop_box = answers.get("crop_box_dimensions")
            crop_ar = answers.get("cropped_aspect_ratio")
            if crop_box and crop_ar:
                print("  " + field_text("crop box", f"{crop_box[0]}x{crop_box[1]} (AR {crop_ar:.4f})", Color.ORANGE))
        else:
            print("  " + field_text("crop", "no", Color.GREEN))
        print("  " + field_text("video bitrate", str(answers.get("video_bitrate_kbps") or "source/default") + " kbps", Color.YELLOW))
        print("  " + field_text("resolution", format_resolution_summary(answers.get("resolution")), Color.MAGENTA))
        if answers.get("final_resolution"):
            final_w, final_h = answers["final_resolution"]
            print("  " + field_text("final output resolution", f"{final_w}x{final_h}", Color.LIME))
        print("  " + field_text("fps", answers.get("fps") or "source", Color.MAGENTA))
        if video_speed_transform_enabled(answers):
            print("  " + field_text("video speed", f"{encode_video_speed_factor(answers) * 100:.0f}%", Color.MAGENTA))
            print("  " + field_text("reverse video", "yes" if answers.get("reverse_video") else "no", Color.ORANGE))
            if answers.get("reverse_video"):
                note(
                    "FFmWiz runs reverse video in short segments to avoid buffering the whole clip in RAM. "
                    "The single command above is an equivalent simple reference command."
                )
    if answers.get("audio_streams"):
        print("  " + field_text("audio tracks", answers.get("audio_tracks"), Color.LIGHT_BLUE))
        print("  " + field_text("audio codec", answers.get("audio_codec"), Color.CYAN))
        print("  " + field_text("audio bitrate", str(answers.get("audio_bitrate_kbps") or "source/default") + " kbps", Color.YELLOW))
        if audio_cut_transform_enabled(answers):
            print(paint(format_cut_ranges_for_summary(answers["audio_cut_keep_ranges"], 25.0, "audio cuts (keep ranges)"), Color.LIME))
        if audio_speed_transform_enabled(answers):
            print("  " + field_text("audio speed", f"{encode_audio_speed_factor(answers) * 100:.0f}%", Color.MAGENTA))
            print("  " + field_text("reverse audio", "yes" if encode_audio_reverse_enabled(answers) else "no", Color.ORANGE))
    if output_has_video(answers) and answers.get("subtitle_streams"):
        print("  " + field_text("subtitle tracks", answers.get("subtitle_tracks"), Color.WHITE))
    cut_keep_ranges = answers.get("cut_keep_ranges") or []
    if cut_keep_ranges:
        fps = get_video_fps(answers)
        print(paint(
            format_cut_ranges_for_summary(cut_keep_ranges, fps, "cuts (keep ranges)"),
            Color.LIME,
        ))


def graphical_hint(text: str) -> str:
    if USE_COLOR:
        return f"{Color.AQUA}{text}{Color.RESET}{Color.HINT_YELLOW}"
    return text


def run_mode_steps(answers: dict[str, Any], steps: list[Step]) -> None:
    idx = 0
    while idx < len(steps):
        if not steps[idx].applicable(answers):
            idx += 1
            continue
        try:
            answers["_question_number"] = idx + 1
            steps[idx].run(answers)
            idx += 1
        except Back:
            if idx == 0:
                raise
            idx -= 1
            while idx > 0 and not steps[idx].applicable(answers):
                idx -= 1


def ensure_video_input(answers: dict[str, Any]) -> None:
    if not answers.get("video_streams"):
        raise ValueError("This mode needs a video stream.")


def ensure_audio_input(answers: dict[str, Any]) -> None:
    if not answers.get("audio_streams"):
        raise ValueError("This mode needs an audio stream.")


def step_video_speed_reverse_options(answers: dict[str, Any]) -> None:
    ensure_video_input(answers)
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Change video speed or reverse video?",
                f"y/n, {graphical_hint('g=Show Graphical Video Speed Editor')}; "
                "y=manual speed/reverse settings",
                "n",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["_speed_reverse_noop"] = True
            return
        if lowered in {"g", "gui", "graphical"}:
            result = open_video_speed_gui(answers)
            if result is None:
                note("Graphical video speed editor was canceled.")
                continue
            answers["speed_factor"] = result["speed"]
            answers["reverse_video"] = result["reverse"]
            answers["include_audio"] = result["include_audio"]
            answers["_speed_reverse_noop"] = False
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = ask_raw(
                    "Enter speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): "
                )
                if speed_text == "0":
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    error(str(exc))
            if "speed_factor" not in answers:
                continue
            answers["reverse_video"] = ask_yes_no("Reverse video too? (y/n) [n]: ", False)
            include_default = bool(answers.get("audio_streams"))
            answers["include_audio"] = ask_yes_no("Sync all audio tracks with the video speed/reverse change? (y/n) [y]: ", include_default)
            answers["_speed_reverse_noop"] = False
            return
        error("Enter y, n, or g.")


def step_video_speed_reverse_for_encode(answers: dict[str, Any]) -> None:
    for key in ("video_speed_enabled", "video_speed_factor", "reverse_video", "audio_speed_from_video"):
        answers.pop(key, None)
    if answers.get("_unified_video_editor_used"):
        speed = clamp_speed_factor(answers.get("_unified_video_speed", DEFAULT_SPEED_FACTOR))
        reverse = bool(answers.get("_unified_reverse_video"))
        answers["video_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
        answers["video_speed_factor"] = speed
        answers["reverse_video"] = reverse
        answers["audio_speed_from_video"] = bool(answers.get("_unified_include_audio"))
        if answers["video_speed_enabled"]:
            print(paint(f"Applied unified speed/reverse: {speed:.2f}x, reverse={'yes' if reverse else 'no'}", Color.LIME))
        return
    allow_gui = not answers.get("_disable_graphical_editors")
    while True:
        hint = (
            f"y/n, {graphical_hint('g=Show Graphical Video Speed Editor')}; "
            "speed/reverse requires video re-encoding"
            if allow_gui
            else "y/n; speed/reverse requires video re-encoding"
        )
        value = ask_raw(question_prompt(answers, "Change video speed or reverse video?", hint, "n"))
        if value == "0":
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["video_speed_enabled"] = False
            return
        if lowered in {"g", "gui", "graphical"}:
            if not allow_gui:
                error("Graphical video speed editor is not available in Folder Encode.")
                continue
            note("Loading Graphical Video Speed Editor...")
            sys.stdout.flush()
            result = open_video_speed_gui(answers)
            if result is None:
                note("Graphical video speed editor was canceled. Returning to the speed question.")
                continue
            answers["video_speed_enabled"] = True
            answers["video_speed_factor"] = result["speed"]
            answers["reverse_video"] = result["reverse"]
            answers["audio_speed_from_video"] = bool(result.get("include_audio"))
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = ask_raw("Enter video speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): ")
                if speed_text == "0":
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["video_speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    error(str(exc))
            if "video_speed_factor" not in answers:
                continue
            answers["reverse_video"] = ask_yes_no("Reverse video too? (y/n) [n]: ", False)
            if answers.get("audio_streams"):
                answers["audio_speed_from_video"] = ask_yes_no("Apply the same speed/reverse to selected audio too? (y/n) [y]: ", True)
            answers["video_speed_enabled"] = True
            return
        error("Enter y, n, or g." if allow_gui else "Enter y or n.")


def step_audio_speed_reverse_options(answers: dict[str, Any]) -> None:
    audio_index = int(answers.get("audio_index", 0))
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Change audio speed or reverse audio?",
                f"y/n, {graphical_hint('g=Show Graphical Audio Speed Editor')}; "
                "y=manual speed/reverse settings",
                "n",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["_speed_reverse_noop"] = True
            return
        if lowered in {"g", "gui", "graphical"}:
            result = open_audio_speed_gui(answers, audio_index)
            if result is None:
                note("Graphical audio speed editor was canceled.")
                continue
            answers["speed_factor"] = result["speed"]
            answers["reverse_audio"] = result["reverse"]
            answers["_speed_reverse_noop"] = False
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = ask_raw(
                    "Enter speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): "
                )
                if speed_text == "0":
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    error(str(exc))
            if "speed_factor" not in answers:
                continue
            answers["reverse_audio"] = ask_yes_no("Reverse audio too? (y/n) [n]: ", False)
            answers["_speed_reverse_noop"] = False
            return
        error("Enter y, n, or g.")


def step_audio_speed_reverse_for_encode(answers: dict[str, Any]) -> None:
    for key in ("audio_speed_enabled", "audio_speed_factor", "reverse_audio"):
        answers.pop(key, None)
    allow_gui = not answers.get("_disable_graphical_editors")
    selected = selected_audio_streams(answers) if answers.get("audio_tracks") is not None else [0]
    audio_index = selected[0] if selected else 0
    while True:
        suffix = " Enter=n keeps any video-linked audio speed." if answers.get("audio_speed_from_video") else ""
        hint = (
            f"y/n, {graphical_hint('g=Show Graphical Audio Speed Editor')}; applies to selected audio tracks.{suffix}"
            if allow_gui
            else f"y/n; applies to selected audio tracks.{suffix}"
        )
        value = ask_raw(question_prompt(answers, "Change audio speed or reverse audio?", hint, "n"))
        if value == "0":
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["audio_speed_enabled"] = False
            return
        if lowered in {"g", "gui", "graphical"}:
            if not allow_gui:
                error("Graphical audio speed editor is not available in Folder Encode.")
                continue
            note("Loading Graphical Audio Speed Editor...")
            sys.stdout.flush()
            result = open_audio_speed_gui(answers, audio_index)
            if result is None:
                note("Graphical audio speed editor was canceled. Returning to the speed question.")
                continue
            answers["audio_speed_enabled"] = True
            answers["audio_speed_factor"] = result["speed"]
            answers["reverse_audio"] = result["reverse"]
            answers["audio_speed_from_video"] = False
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = ask_raw("Enter audio speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): ")
                if speed_text == "0":
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["audio_speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    error(str(exc))
            if "audio_speed_factor" not in answers:
                continue
            answers["reverse_audio"] = ask_yes_no("Reverse audio too? (y/n) [n]: ", False)
            answers["audio_speed_enabled"] = True
            answers["audio_speed_from_video"] = False
            return
        error("Enter y, n, or g." if allow_gui else "Enter y or n.")


def step_audio_track_for_tool(answers: dict[str, Any]) -> None:
    streams = answers.get("audio_streams") or []
    if not streams:
        raise ValueError("This mode needs an audio stream.")
    if len(streams) == 1:
        answers["audio_index"] = 0
        return
    print()
    print(paint("Audio streams", Color.BOLD + Color.BLUE))
    fmt = answers.get("format", {})
    packet_sizes = get_packet_sizes(answers)
    for idx, stream in enumerate(streams):
        print(
            f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
            f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
            f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
            f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
            f"{field_text('bitrate', describe_bitrate(stream_bitrate_kbps(stream, fmt, packet_sizes)), Color.YELLOW)}"
        )
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Choose audio track",
                "track number 1 is the first audio track",
                "1",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = "1"
        if re.fullmatch(r"\d+", value):
            index = int(value) - 1
            if 0 <= index < len(streams):
                answers["audio_index"] = index
                return
        error(f"Enter an audio track number from 1 to {len(streams)}.")


def step_audio_cut_editor(answers: dict[str, Any]) -> None:
    audio_index = int(answers.get("audio_index", 0))
    while True:
        ranges = open_audio_cut_gui(answers, audio_index)
        if ranges is None:
            note("Graphical audio cut editor was canceled.")
            try_again = ask_yes_no("Open it again? (y/n) [y]: ", True)
            if not try_again:
                answers["_audio_cut_noop"] = True
                return
            continue
        if not ranges:
            error("No valid audio ranges were selected.")
            continue
        answers["audio_keep_ranges"] = ranges
        answers["_audio_cut_noop"] = False
        return


def step_audio_transform_editor(answers: dict[str, Any]) -> None:
    audio_index = int(answers.get("audio_index", 0))
    while True:
        result = open_audio_transform_gui(answers, audio_index)
        if result is None:
            note("Graphical audio transform editor was canceled.")
            try_again = ask_yes_no("Open it again? (y/n) [y]: ", True)
            if not try_again:
                answers["_audio_transform_noop"] = True
                return
            continue
        keep_ranges = list(result.get("keep_ranges") or [])
        speed = clamp_speed_factor(result.get("speed", DEFAULT_SPEED_FACTOR))
        reverse = bool(result.get("reverse"))
        changed = bool(keep_ranges) or reverse or abs(speed - 1.0) > 1e-6
        if not changed:
            note("No audio transform was selected.")
            answers["_audio_transform_noop"] = True
            return
        answers["audio_cut_keep_ranges"] = keep_ranges
        answers["audio_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
        answers["audio_speed_factor"] = speed
        answers["reverse_audio"] = reverse
        answers["_audio_transform_noop"] = False
        return


def step_audio_cut_for_encode(answers: dict[str, Any]) -> None:
    answers.pop("audio_cut_keep_ranges", None)
    allow_gui = not answers.get("_disable_graphical_editors")
    selected = selected_audio_streams(answers) if answers.get("audio_tracks") is not None else [0]
    audio_index = selected[0] if selected else 0
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    while True:
        hint = (
            f"y/n, {graphical_hint('g=Show Graphical Audio Cut Editor')}; applies to selected audio tracks"
            if allow_gui
            else "y/n; terminal range entry applies to selected audio tracks"
        )
        value = ask_raw(question_prompt(answers, "Apply audio waveform cuts?", hint, "n"))
        if value == "0":
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            return
        if lowered in {"g", "gui", "graphical"}:
            if not allow_gui:
                error("Graphical audio cut editor is not available in Folder Encode.")
                continue
            note("Loading Graphical Audio Cut Editor...")
            sys.stdout.flush()
            ranges = open_audio_cut_gui(answers, audio_index)
            if ranges is None:
                note("Graphical audio cut editor was canceled. Returning to the audio cut question.")
                continue
            keep_ranges = normalize_cut_ranges(ranges, duration)
        elif lowered in {"y", "yes"}:
            try:
                keep_ranges = collect_cut_ranges_terminal(answers, 25.0, duration)
            except Back:
                continue
        else:
            error("Enter y, n, or g." if allow_gui else "Enter y or n.")
            continue
        if not keep_ranges:
            note("No audio keep ranges were produced; audio cuts disabled.")
            return
        answers["audio_cut_keep_ranges"] = keep_ranges
        print(paint(format_audio_ranges_for_summary(keep_ranges, "Audio cuts (keep ranges)"), Color.LIME))
        return


def print_transform_summary(answers: dict[str, Any], cmd: list[str], title: str) -> None:
    print()
    print(paint(f"{title} summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("input", answers["input_path"], Color.WHITE))
    print("  " + field_text("output", answers["output_path"], Color.LIME))
    if "speed_factor" in answers:
        print("  " + field_text("speed", f"{float(answers['speed_factor']) * 100:.0f}%", Color.MAGENTA))
    if "audio_speed_factor" in answers:
        print("  " + field_text("audio speed", f"{float(answers['audio_speed_factor']) * 100:.0f}%", Color.MAGENTA))
    if "reverse_video" in answers:
        print("  " + field_text("reverse video", "yes" if answers.get("reverse_video") else "no", Color.ORANGE))
        print("  " + field_text("audio", "all tracks synced" if answers.get("include_audio") else "none", Color.BLUE))
        if answers.get("reverse_video"):
            note(
                "FFmWiz runs reverse video in short segments to avoid buffering the whole clip in RAM. "
                "The single command above is an equivalent simple reference command."
            )
    if "reverse_audio" in answers:
        print("  " + field_text("reverse audio", "yes" if answers.get("reverse_audio") else "no", Color.ORANGE))
        print("  " + field_text("audio track", answers.get("audio_index", 0), Color.BLUE))
    if "audio_keep_ranges" in answers:
        print("  " + field_text("audio track", answers.get("audio_index", 0), Color.BLUE))
        print(paint(format_audio_ranges_for_summary(answers["audio_keep_ranges"], "audio keep ranges"), Color.LIME))
    if "audio_cut_keep_ranges" in answers:
        print("  " + field_text("audio track", answers.get("audio_index", 0), Color.BLUE))
        print(paint(format_audio_ranges_for_summary(answers["audio_cut_keep_ranges"], "audio keep ranges"), Color.LIME))
    print()
    print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
    log_info("Final PowerShell command: " + command_to_powershell(cmd))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))


def step_video_speed_start_now(answers: dict[str, Any]) -> None:
    if answers.get("_speed_reverse_noop"):
        return
    cmd = build_video_speed_reverse_command(answers)
    answers["cmd"] = cmd
    print_transform_summary(answers, cmd, "Video Speed / Reverse")
    answers["start_now"] = ask_yes_no(
        question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def step_audio_speed_start_now(answers: dict[str, Any]) -> None:
    if answers.get("_speed_reverse_noop"):
        return
    cmd = build_audio_speed_reverse_command(answers)
    answers["cmd"] = cmd
    print_transform_summary(answers, cmd, "Audio Speed / Reverse")
    answers["start_now"] = ask_yes_no(
        question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def step_audio_cut_start_now(answers: dict[str, Any]) -> None:
    if answers.get("_audio_cut_noop"):
        return
    cmd = build_audio_cut_command(answers)
    answers["cmd"] = cmd
    print_transform_summary(answers, cmd, "Audio Cut")
    answers["start_now"] = ask_yes_no(
        question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def step_audio_transform_start_now(answers: dict[str, Any]) -> None:
    if answers.get("_audio_transform_noop"):
        return
    cmd = build_audio_transform_command(answers)
    answers["cmd"] = cmd
    print_transform_summary(answers, cmd, "Audio Cut / Speed / Reverse")
    answers["start_now"] = ask_yes_no(
        question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def ask_main_menu(answers: dict[str, Any], config_path: Path) -> int:
    print()
    print(paint("FFmWiz Main menu:", Color.BOLD + Color.LIGHT_BLUE))
    print(f"  {paint('1.', Color.LIGHT_BLUE)} Interactive wizard {paint('[1]', Color.GREEN)}")
    print(f"  {paint('2.', Color.LIGHT_BLUE)} Load config and ask crop only")
    print(f"  {paint('3.', Color.LIGHT_BLUE)} Cut video only with copy")
    print(f"  {paint('4.', Color.LIGHT_BLUE)} Folder Encode")
    print(f"  {paint('5.', Color.LIGHT_BLUE)} Add files to video")
    print(f"  {paint('6.', Color.LIGHT_BLUE)} Media info report")
    print(f"  {paint('7.', Color.LIGHT_BLUE)} Stream Cleanup Remux")
    print(f"  {paint('8.', Color.LIGHT_BLUE)} Hard Sub Encode")
    print(f"  {paint('9.', Color.LIGHT_BLUE)} Video Speed / Reverse")
    print(f"  {paint('10.', Color.LIGHT_BLUE)} Audio Cut / Speed / Reverse")
    print()
    # The main menu has no previous step, so '0=back' is intentionally not
    # advertised. Submenus continue to support 0=back where it makes sense.
    while True:
        value = ask_raw(
            f"{paint('Selection', Color.BOLD)} {paint('[1]', Color.GREEN)} "
            f"{back_text('quit=exit')}: "
        )
        if not value:
            return 1
        if value in {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}:
            return int(value)
        error("Enter a menu number from 1 to 10.")


# Kept as a thin wrapper for backwards compatibility with any external caller.
def ask_start_mode(answers: dict[str, Any], config_path: Path) -> int:
    return ask_main_menu(answers, config_path)


def run_crop_only_prompt(answers: dict[str, Any]) -> None:
    if not output_has_video(answers):
        note("This output has no video stream, so crop selection was skipped.")
        return

    steps = [
        Step("crop_enabled", output_has_video, step_crop_enabled),
        Step("crop_top", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_top),
        Step("crop_left", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_left),
        Step("crop_right", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_right),
        Step("crop_bottom", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_bottom),
    ]

    def next_index(start: int) -> int:
        idx = start
        while idx < len(steps) and not steps[idx].applicable(answers):
            idx += 1
        return idx

    def prev_index(start: int) -> int:
        idx = start
        while idx > 0 and not steps[idx].applicable(answers):
            idx -= 1
        return max(0, idx)

    idx = next_index(0)
    while idx < len(steps):
        try:
            answers["_question_number"] = answers.get("_question_offset", 0) + idx + 1
            answers["_last_question_number"] = answers["_question_number"]
            steps[idx].run(answers)
            idx = next_index(idx + 1)
        except Back:
            if idx == 0:
                raise
            idx = prev_index(idx - 1)


# ===================================================================
# "Cut video only with copy" mode and cut helpers shared with the
# interactive wizard re-encode path. Stream-copy cuts are very fast
# and lossless but cut boundaries snap to nearby keyframes. The
# re-encode path inside the wizard uses filter_complex/trim/atrim/
# concat for frame-accurate cuts.
# ===================================================================

COPY_CUT_WARNING = (
    "Stream-copy cutting is very fast and keeps original quality, but cut "
    "points may snap to nearby keyframes. For exact frame-accurate cutting, "
    "use re-encode mode."
)


def ask_hmsf_time(
    answers: dict[str, Any],
    title: str,
    fps: float,
    duration: float | None = None,
    default: str | None = None,
    details_extra: str = "",
) -> float:
    """Prompt for a single h:m:s:frame time and return seconds."""
    base_details = f"h:m:s:frame at fps={fps:.3f}; example: 00:01:30:12"
    if duration:
        base_details += f"; clip duration ~ {format_duration(duration)}"
    if details_extra:
        base_details += f"; {details_extra}"
    while True:
        value = ask_raw(question_prompt(answers, title, base_details, default))
        if value == "0":
            raise Back()
        if not value:
            if default is None:
                error("Enter a time value in h:m:s:frame.")
                continue
            value = default
        try:
            seconds = parse_hmsf_time(value, fps)
        except ValueError as exc:
            error(str(exc))
            continue
        if duration and seconds > duration + 1.0:
            error(
                f"Time {seconds_to_hmsf(seconds, fps)} ({seconds:.3f}s) is past the file "
                f"duration ({format_duration(duration)}). Try again."
            )
            continue
        return seconds


def ask_cut_method(answers: dict[str, Any]) -> int:
    """Ask the user how to define cut ranges.

    Returns:
        1 -> open the temporary GUI cut editor (default)
        2 -> enter cut times manually with h:m:s:frame
    """
    print()
    print(paint("Cut video only with copy:", Color.BOLD + Color.LIGHT_BLUE))
    print(f"  {paint('1.', Color.LIGHT_BLUE)} GUI cut editor {paint('[default]', Color.GREEN)}")
    print(f"  {paint('2.', Color.LIGHT_BLUE)} Manual cut using h:m:s:frame")
    print()
    while True:
        value = ask_raw(
            f"{paint('Selection', Color.BOLD)} {paint('[1]', Color.GREEN)} "
            f"{back_text('0=back, quit=exit')}: "
        )
        if not value:
            return 1
        if value == "0":
            raise Back()
        if value in {"1", "2"}:
            return int(value)
        error("Enter 1 or 2.")


def ask_manual_cut_layout(answers: dict[str, Any]) -> int:
    """Ask which manual cut layout the user wants. Returns 1..4."""
    print()
    print(paint("Manual cut mode:", Color.BOLD + Color.LIGHT_BLUE))
    print(f"  {paint('1.', Color.LIGHT_BLUE)} Keep one range {paint('[1]', Color.GREEN)}")
    print(f"  {paint('2.', Color.LIGHT_BLUE)} Remove one range")
    print(f"  {paint('3.', Color.LIGHT_BLUE)} Remove multiple ranges")
    print(f"  {paint('4.', Color.LIGHT_BLUE)} Keep multiple ranges")
    print()
    while True:
        value = ask_raw(
            f"{paint('Selection', Color.BOLD)} {paint('[1]', Color.GREEN)} "
            f"{back_text('0=back, quit=exit')}: "
        )
        if not value:
            return 1
        if value == "0":
            raise Back()
        if value in {"1", "2", "3", "4"}:
            return int(value)
        error("Enter 1, 2, 3, or 4.")


def collect_cut_ranges_terminal(
    answers: dict[str, Any],
    fps: float,
    duration: float,
) -> list[tuple[float, float]]:
    """Run the terminal manual-cut flow. Returns the final keep_ranges list."""
    layout = ask_manual_cut_layout(answers)

    if layout == 1:
        start = ask_hmsf_time(answers, "Keep start time", fps, duration)
        end = ask_hmsf_time(answers, "Keep end time", fps, duration)
        if end <= start:
            error("End must be greater than start.")
            return []
        return normalize_cut_ranges([(start, end)], duration or end)

    if layout == 2:
        start = ask_hmsf_time(answers, "Cut start time", fps, duration)
        end = ask_hmsf_time(answers, "Cut end time", fps, duration)
        if end <= start:
            error("End must be greater than start.")
            return []
        return invert_cut_ranges_to_keep_ranges([(start, end)], duration)

    if layout == 3:
        removes: list[tuple[float, float]] = []
        idx = 1
        while True:
            print()
            print(paint(f"Cut range #{idx}", Color.BOLD + Color.ORANGE))
            start = ask_hmsf_time(answers, "  Cut start", fps, duration)
            end = ask_hmsf_time(answers, "  Cut end", fps, duration)
            if end <= start:
                error("End must be greater than start; this range was ignored.")
            else:
                removes.append((start, end))
            again = ask_yes_no("Add another remove range? [y/N]", False)
            if not again:
                break
            idx += 1
        return invert_cut_ranges_to_keep_ranges(removes, duration)

    # layout == 4
    keeps: list[tuple[float, float]] = []
    idx = 1
    while True:
        print()
        print(paint(f"Keep range #{idx}", Color.BOLD + Color.LIME))
        start = ask_hmsf_time(answers, "  Keep start", fps, duration)
        end = ask_hmsf_time(answers, "  Keep end", fps, duration)
        if end <= start:
            error("End must be greater than start; this range was ignored.")
        else:
            keeps.append((start, end))
        again = ask_yes_no("Add another keep range? [y/N]", False)
        if not again:
            break
        idx += 1
    return normalize_cut_ranges(keeps, duration or (keeps[-1][1] if keeps else 0.0))


def format_cut_ranges_for_summary(
    ranges: list[tuple[float, float]],
    fps: float,
    label: str,
) -> str:
    if not ranges:
        return f"{label}: (none)"
    lines = [f"{label}:"]
    for idx, (start, end) in enumerate(ranges, start=1):
        lines.append(
            f"  {idx}. {seconds_to_hmsf(start, fps)} -> {seconds_to_hmsf(end, fps)}  "
            f"({seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)})"
        )
    return "\n".join(lines)


def format_audio_ranges_for_summary(ranges: list[tuple[float, float]], label: str) -> str:
    if not ranges:
        return f"{label}: (none)"
    lines = [f"{label}:"]
    for idx, (start, end) in enumerate(ranges, start=1):
        lines.append(f"  {idx}. {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}")
    return "\n".join(lines)


def print_cut_summary(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    remove_ranges: list[tuple[float, float]],
    fps: float,
    duration: float,
    mode_label: str,
    output_path: Path,
) -> None:
    print()
    print(paint("Cut summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("Mode", mode_label, Color.CYAN))
    print("  " + field_text("Input", answers["input_path"], Color.WHITE))
    print("  " + field_text("Output", output_path, Color.LIME))
    print("  " + field_text("Detected FPS", f"{fps:.3f}", Color.MAGENTA))
    print("  " + field_text("Source duration", format_duration(duration), Color.MAGENTA))
    print("  " + field_text("Kept duration", format_duration(total_keep_duration(keep_ranges)), Color.GREEN))
    if remove_ranges:
        print(paint(format_cut_ranges_for_summary(remove_ranges, fps, "Removed ranges"), Color.ORANGE))
    print(paint(format_cut_ranges_for_summary(keep_ranges, fps, "Kept ranges"), Color.LIGHT_BLUE))


def ask_continue_default_yes(answers: dict[str, Any]) -> bool:
    """Ask the user 'Continue? [Y/n]'. Default Yes; pressing Enter continues.

    Returns True to continue, False to cancel. Raises Back when the user enters
    '0' so callers can decide how to handle the previous-step navigation.
    Callers MUST wrap this in try/except Back when they want to return to a
    higher-level menu instead of crashing.
    """
    while True:
        value = ask_raw(
            f"{paint('Continue?', Color.BOLD)} {paint('[Y/n]', Color.GREEN)} "
            f"{back_text('0=back, quit=exit')}: "
        )
        if value == "0":
            raise Back()
        if not value:
            return True
        lowered = value.lower()
        if lowered in {"y", "yes"}:
            return True
        if lowered in {"n", "no"}:
            return False
        error("Enter y or n. Default on Enter: Y (continue).")


# Backwards-compatible alias. The original name promised "default No" but the
# project's UX direction is now "default Yes". The alias is kept so any future
# external callers still resolve, but it returns the same default-Yes prompt.
ask_continue_yes_no_default_no = ask_continue_default_yes


def copy_cut_chapter_seconds(chapter: dict[str, Any], prefix: str) -> float | None:
    time_value = chapter.get(f"{prefix}_time")
    if time_value not in (None, ""):
        try:
            return float(time_value)
        except (TypeError, ValueError):
            pass
    raw_value = chapter.get(prefix)
    if raw_value in (None, ""):
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    time_base = str(chapter.get("time_base") or "").strip()
    match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", time_base)
    if not match:
        return value
    numerator = int(match.group(1))
    denominator = int(match.group(2))
    if denominator <= 0:
        return value
    return value * numerator / denominator


def load_copy_cut_chapters(answers: dict[str, Any]) -> list[dict[str, Any]]:
    probe = answers.get("probe") if isinstance(answers.get("probe"), dict) else {}
    chapters = (probe or {}).get("chapters") or []
    if chapters:
        return [chapter for chapter in chapters if isinstance(chapter, dict)]

    ffprobe = answers.get("ffprobe")
    input_path = answers.get("input_path")
    if not ffprobe or not input_path:
        return []
    try:
        payload = ffprobe_full_json(str(ffprobe), Path(input_path))
    except Exception:
        log_exception(f"Copy Cut chapter probe failed for {input_path}")
        return []
    answers["probe"] = payload
    if payload.get("format"):
        answers["format"] = payload.get("format") or answers.get("format", {})
    chapters = payload.get("chapters") or []
    return [chapter for chapter in chapters if isinstance(chapter, dict)]


def copy_cut_source_duration(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    chapters: list[dict[str, Any]],
) -> float:
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    for start, end in keep_ranges:
        duration = max(duration, float(start or 0.0), float(end or 0.0))
    for chapter in chapters:
        end = copy_cut_chapter_seconds(chapter, "end")
        if end is not None:
            duration = max(duration, end)
    return max(0.0, duration)


def copy_cut_removed_ranges(
    keep_ranges: list[tuple[float, float]],
    duration: float,
) -> list[tuple[float, float]]:
    keep = normalize_cut_ranges(keep_ranges, duration)
    removed: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in keep:
        if start > cursor + 1e-6:
            removed.append((cursor, start))
        cursor = max(cursor, end)
    if duration > 0 and cursor < duration - 1e-6:
        removed.append((cursor, duration))
    return removed


def copy_cut_chapter_overlaps_removed(
    chapter_start: float,
    chapter_end: float,
    removed_ranges: list[tuple[float, float]],
) -> bool:
    return any(chapter_start < removed_end and chapter_end > removed_start for removed_start, removed_end in removed_ranges)


def copy_cut_remap_chapter(
    chapter_start: float,
    chapter_end: float,
    keep_ranges: list[tuple[float, float]],
) -> tuple[float, float] | None:
    output_offset = 0.0
    for keep_start, keep_end in keep_ranges:
        if chapter_start >= keep_start - 1e-6 and chapter_end <= keep_end + 1e-6:
            new_start = output_offset + max(0.0, chapter_start - keep_start)
            new_end = output_offset + max(0.0, chapter_end - keep_start)
            if new_end > new_start:
                return new_start, new_end
            return None
        output_offset += max(0.0, keep_end - keep_start)
    return None


def ffmetadata_escape(value: Any) -> str:
    text = str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    for char in ("=", ";", "#"):
        text = text.replace(char, "\\" + char)
    return text


def analyze_copy_cut_chapter_plan(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
) -> dict[str, Any]:
    chapters = load_copy_cut_chapters(answers)
    if not chapters:
        return {"mode": "copy", "chapters": [], "overlap_count": 0}

    duration = copy_cut_source_duration(answers, keep_ranges, chapters)
    normalized_keep = normalize_cut_ranges(keep_ranges, duration)
    removed_ranges = copy_cut_removed_ranges(normalized_keep, duration)
    if not removed_ranges:
        return {"mode": "copy", "chapters": [], "overlap_count": 0}

    overlap_count = 0
    remapped_chapters: list[dict[str, Any]] = []
    for chapter in chapters:
        start = copy_cut_chapter_seconds(chapter, "start")
        end = copy_cut_chapter_seconds(chapter, "end")
        if start is None or end is None or end <= start:
            log_debug(f"Copy Cut skipped invalid chapter while rebuilding: {chapter!r}")
            continue
        if copy_cut_chapter_overlaps_removed(start, end, removed_ranges):
            overlap_count += 1
            continue
        remapped = copy_cut_remap_chapter(start, end, normalized_keep)
        if remapped is None:
            continue
        new_start, new_end = remapped
        remapped_chapters.append({
            "start": new_start,
            "end": new_end,
            "metadata": dict(chapter.get("tags") or {}),
        })

    if overlap_count == 0:
        return {"mode": "copy", "chapters": [], "overlap_count": 0}
    if not remapped_chapters:
        log_info(f"Copy Cut chapter plan: dropping all chapters; overlap_count={overlap_count}")
        return {"mode": "drop", "chapters": [], "overlap_count": overlap_count}
    log_info(
        "Copy Cut chapter plan: rebuilding chapters; "
        f"kept={len(remapped_chapters)} dropped={overlap_count}"
    )
    return {"mode": "metadata", "chapters": remapped_chapters, "overlap_count": overlap_count}


def write_copy_cut_chapter_metadata(plan: dict[str, Any], temp_dir: Path) -> Path:
    metadata_path = temp_dir / "chapters.ffmetadata"
    lines = [";FFMETADATA1", ""]
    for chapter in plan.get("chapters") or []:
        start_ms = max(0, int(round(float(chapter["start"]) * 1000)))
        end_ms = max(start_ms + 1, int(round(float(chapter["end"]) * 1000)))
        lines.extend([
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start_ms}",
            f"END={end_ms}",
        ])
        for key, value in (chapter.get("metadata") or {}).items():
            if value is None:
                continue
            lines.append(f"{ffmetadata_escape(key)}={ffmetadata_escape(value)}")
        lines.append("")
    metadata_path.write_text("\n".join(lines), encoding="utf-8")
    return metadata_path


def prepare_copy_cut_chapter_plan(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    temp_dir: Path | None = None,
) -> dict[str, Any]:
    plan = analyze_copy_cut_chapter_plan(answers, keep_ranges)
    if plan.get("mode") == "metadata":
        if temp_dir is None:
            raise ValueError("A temporary directory is required to rebuild Copy Cut chapters.")
        plan = dict(plan)
        plan["metadata_path"] = write_copy_cut_chapter_metadata(plan, temp_dir)
    return plan


def copy_cut_chapter_map_args(plan: dict[str, Any] | None, metadata_input_index: int = 1) -> list[str]:
    mode = (plan or {}).get("mode", "copy")
    if mode == "metadata":
        return ["-map_chapters", str(metadata_input_index)]
    if mode == "drop":
        return ["-map_chapters", "-1"]
    return ["-map_chapters", "0"]


def build_copy_cut_range_command(
    ffmpeg: str,
    overwrite: str,
    input_path: Path,
    start: float,
    end: float,
    output_path: Path,
    chapter_plan: dict[str, Any] | None = None,
) -> list[str]:
    duration = max(0.0, float(end) - float(start))
    cmd = [
        ffmpeg, overwrite, "-hide_banner",
        "-ss", seconds_to_ffmpeg_time(start),
        "-i", str(input_path),
    ]
    if chapter_plan and chapter_plan.get("mode") == "metadata":
        cmd.extend(["-i", str(chapter_plan["metadata_path"])])
    cmd.extend(["-t", seconds_to_ffmpeg_time(duration)])
    cmd.extend(["-map", "0", "-map_metadata", "0"])
    cmd.extend(copy_cut_chapter_map_args(chapter_plan, 1))
    cmd.extend(["-c", "copy", "-avoid_negative_ts", "make_zero", str(output_path)])
    return cmd


def build_copy_cut_concat_command(
    ffmpeg: str,
    overwrite: str,
    concat_list: Path,
    output_path: Path,
    chapter_plan: dict[str, Any] | None = None,
) -> list[str]:
    cmd = [
        ffmpeg, overwrite, "-hide_banner",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
    ]
    if chapter_plan and chapter_plan.get("mode") == "metadata":
        cmd.extend(["-i", str(chapter_plan["metadata_path"])])
    cmd.extend(["-map", "0", "-map_metadata", "0"])
    cmd.extend(copy_cut_chapter_map_args(chapter_plan, 1))
    cmd.extend(["-c", "copy", "-avoid_negative_ts", "make_zero", str(output_path)])
    return cmd


def run_copy_cut(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    output_path: Path,
) -> int:
    """Execute stream-copy cuts using one ffmpeg call (single range) or
    segment extraction + concat demuxer (multiple ranges)."""
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    overwrite = "-y" if OVERWRITE_OUTPUT else "-n"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(keep_ranges) == 1:
        start, end = keep_ranges[0]
        temp_context: tempfile.TemporaryDirectory[str] | None = None
        try:
            initial_plan = analyze_copy_cut_chapter_plan(answers, keep_ranges)
            if initial_plan.get("mode") == "metadata":
                temp_context = tempfile.TemporaryDirectory(prefix="ffmwiz_copycut_chapters_")
                chapter_plan = dict(initial_plan)
                chapter_plan["metadata_path"] = write_copy_cut_chapter_metadata(chapter_plan, Path(temp_context.name))
            else:
                chapter_plan = initial_plan
            cmd = build_copy_cut_range_command(ffmpeg, overwrite, input_path, start, end, output_path, chapter_plan)
            print()
            print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
            print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
            print()
            print(paint("Starting FFmpeg...", Color.GREEN))
            rc, _ = run_ffmpeg_with_progress(cmd, total_duration=max(0.0, end - start),
                                              label="Stream-copy cut")
            return rc
        finally:
            if temp_context is not None:
                temp_context.cleanup()

    # Multiple ranges: extract segments to MKV intermediates and concat.
    with tempfile.TemporaryDirectory(prefix="ffmwiz_copycut_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        chapter_plan = prepare_copy_cut_chapter_plan(answers, keep_ranges, tmpdir)
        segment_chapter_map = "0" if chapter_plan.get("mode") == "copy" else "-1"
        segments: list[Path] = []
        seg_ext = "mkv"  # MKV is the safest concat-with-copy intermediate.
        for idx, (start, end) in enumerate(keep_ranges):
            seg_path = tmpdir / f"seg_{idx:04d}.{seg_ext}"
            duration = max(0.0, end - start)
            cmd = [
                ffmpeg, overwrite, "-hide_banner",
                "-ss", seconds_to_ffmpeg_time(start),
                "-i", str(input_path),
                "-t", seconds_to_ffmpeg_time(duration),
                "-map", "0",
                "-map_metadata", "0",
                "-map_chapters", segment_chapter_map,
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                str(seg_path),
            ]
            print()
            note(f"Segment {idx + 1}/{len(keep_ranges)}: {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}")
            print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
            rc, _ = run_ffmpeg_with_progress(
                cmd, total_duration=max(0.0, end - start),
                label=f"Segment {idx + 1}/{len(keep_ranges)}",
            )
            if rc != 0:
                error(f"Segment extraction failed for range #{idx + 1}.")
                return rc
            segments.append(seg_path)

        # Build the concat demuxer list file. Path entries use forward slashes
        # and single-quoted strings; ' inside a path is escaped as '\''.
        concat_list = tmpdir / "concat.txt"
        with concat_list.open("w", encoding="utf-8") as handle:
            for seg in segments:
                escaped = seg.as_posix().replace("'", "'\\''")
                handle.write(f"file '{escaped}'\n")

        cmd = build_copy_cut_concat_command(ffmpeg, overwrite, concat_list, output_path, chapter_plan)
        print()
        note("Concatenating segments with -f concat -c copy...")
        print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
        total_seg_duration = sum(max(0.0, e - s) for s, e in keep_ranges)
        rc, _ = run_ffmpeg_with_progress(
            cmd, total_duration=total_seg_duration, label="Concat segments",
        )
        return rc


def run_copy_cut_mode(
    base_answers: dict[str, Any],
) -> tuple[int, float] | None:
    """Top-level driver for main-menu option 3.

    Only Back from the first Copy Cut question bubbles up to the main menu.
    Later questions handle Back locally so 0 moves one step backward.
    """
    try:
        return _run_copy_cut_mode_impl(base_answers)
    except Back:
        note("Returning to main menu.")
        return None


def _run_copy_cut_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    answers["_question_offset"] = 1
    answers.pop("cut_keep_ranges", None)

    keep_ranges: list[tuple[float, float]] = []
    output_path: Path | None = None
    fps = 25.0
    duration = 0.0
    stage = 0
    while True:
        if stage == 0:
            try:
                answers["_question_number"] = 1
                step_input_path(answers)
            except Back:
                raise
            if not answers.get("video_streams"):
                error("Copy cut mode requires a video stream.")
                continue
            fps = get_video_fps(answers)
            duration = stream_duration_seconds({}, answers.get("format")) or 0.0
            stage = 1
            continue

        if stage == 1:
            try:
                answers["_question_number"] = 2
                step_output_location(answers)
            except Back:
                stage = 0
                continue
            input_path: Path = answers["input_path"]
            answers["output_ext"] = input_path.suffix.lstrip(".") or "mkv"
            stage = 2
            continue

        if stage == 2:
            note(COPY_CUT_WARNING)
            answers["_question_number"] = 3
            try:
                method = ask_cut_method(answers)
            except Back:
                stage = 1
                continue
            if method == 1:
                gui_ranges = open_cut_gui(answers, fps=fps, duration=duration)
                if gui_ranges is None:
                    if answers.pop("_last_gui_error", None) == "cut":
                        note("GUI cut editor failed. Returning to the cut-method menu.")
                    else:
                        note("GUI cut editor was canceled. Returning to the cut-method menu.")
                    continue
                keep_ranges = normalize_cut_ranges(gui_ranges, duration)
            else:
                try:
                    keep_ranges = collect_cut_ranges_terminal(answers, fps, duration)
                except Back:
                    note("Returning to the cut-method menu.")
                    continue
            if not keep_ranges:
                note("No keep ranges were produced. Returning to the cut-method menu.")
                continue
            stage = 3
            continue

        answers["output_collision_suffix"] = "_cut"
        output_path = build_output_path(answers)
        answers["output_path"] = output_path

        remove_ranges = invert_cut_ranges_to_keep_ranges(keep_ranges, duration) if duration > 0 else []
        print_cut_summary(answers, keep_ranges, remove_ranges, fps, duration, "Stream copy", output_path)

        try:
            proceed = ask_continue_default_yes(answers)
        except Back:
            stage = 2
            continue
        if not proceed:
            note("Operation canceled by user.")
            return None
        break

    started_at = time.perf_counter()
    if output_path is None:
        raise RuntimeError("Copy Cut output path was not resolved.")
    return_code = run_copy_cut(answers, keep_ranges, output_path)
    elapsed = time.perf_counter() - started_at
    return return_code, elapsed


# -------------------------------------------------------------------
# Re-encode cut path used by the interactive wizard. Cuts are applied
# via -filter_complex with trim/atrim/concat for frame accuracy.
# -------------------------------------------------------------------

def step_cuts(answers: dict[str, Any]) -> None:
    """Optional wizard step: ask the user whether to define cuts before
    re-encoding. Stores answers['cut_keep_ranges'] when active."""
    answers.pop("cut_keep_ranges", None)
    fps = get_video_fps(answers)
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    if answers.get("_unified_video_editor_used"):
        keep_ranges = normalize_cut_ranges(list(answers.get("_unified_cut_keep_ranges") or []), duration)
        if keep_ranges and not (len(keep_ranges) == 1 and keep_ranges[0][0] <= 1e-6 and keep_ranges[0][1] >= duration - 1e-6):
            answers["cut_keep_ranges"] = keep_ranges
            print(paint(
                format_cut_ranges_for_summary(keep_ranges, fps, "Unified cuts (keep ranges)"),
                Color.LIME,
            ))
        return
    while True:
        allow_gui = not answers.get("_disable_graphical_editors")
        if allow_gui and USE_COLOR:
            gui_hint = f"{Color.AQUA}g=Show Graphical Cut Editor{Color.RESET}{Color.HINT_YELLOW}"
        elif allow_gui:
            gui_hint = "g=Show Graphical Cut Editor"
        else:
            gui_hint = ""
        hint_text = (
            f"y/n, {gui_hint}; cuts are applied frame-accurate via filter_complex"
            if allow_gui
            else "y/n; cuts are applied frame-accurate via filter_complex"
        )
        value = ask_raw(
            question_prompt(
                answers,
                "Apply cuts before encoding?",
                hint_text,
                "n",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            return
        if lowered in {"y", "yes"}:
            try:
                keep_ranges = collect_cut_ranges_terminal(answers, fps, duration)
            except Back:
                continue
        elif lowered in {"g", "gui", "preview"}:
            if not allow_gui:
                error("Graphical cut editor is not available in Folder Encode.")
                continue
            note("Loading Graphical Cut Editor...")
            sys.stdout.flush()
            gui_ranges = open_cut_gui(answers, fps=fps, duration=duration)
            if gui_ranges is None:
                if answers.pop("_last_gui_error", None) == "cut":
                    note("GUI cut editor failed. Returning to the cut question.")
                    continue
                else:
                    note("GUI cut editor was canceled. Returning to the cut question.")
                    continue
            keep_ranges = normalize_cut_ranges(gui_ranges, duration)
        else:
            error("Enter y, n, or g." if allow_gui else "Enter y or n.")
            continue
        if not keep_ranges:
            note("No keep ranges were produced; cuts disabled.")
            return
        answers["cut_keep_ranges"] = keep_ranges
        print(paint(
            format_cut_ranges_for_summary(keep_ranges, fps, "Cuts (keep ranges)"),
            Color.LIME,
        ))
        return


def step_folder_input_path(answers: dict[str, Any]) -> None:
    while True:
        folder_example = example_text(r"D:\Videos\Season 01")
        value = ask_required(
            question_prompt(
                answers,
                "Enter input folder path",
                f"drag and drop a folder here or paste a path; example: {folder_example}",
            )
        )
        folder_path = terminal_path(value)
        if not folder_path.exists() or not folder_path.is_dir():
            error("Folder not found. Enter the full folder path again.")
            continue
        answers["folder_input_path"] = folder_path
        return


def step_folder_output_location(answers: dict[str, Any]) -> None:
    input_folder: Path = answers["folder_input_path"]
    default_output = folder_default_output_path(input_folder)
    folder_example = example_text(r"E:\output")
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Enter output folder",
                f"Enter=create sibling folder named {default_output.name}; example: {folder_example}",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            output_folder = default_output
        else:
            output_folder = terminal_path(value)
            if not output_folder.is_absolute():
                output_folder = input_folder.parent / output_folder
        if output_folder.exists() and not output_folder.is_dir():
            error("Output path exists and is not a folder. Enter a different folder.")
            continue
        answers["folder_output_location"] = output_folder
        answers["output_location"] = output_folder
        answers.pop("output_name_stem", None)
        return


def run_folder_input_output_steps(answers: dict[str, Any]) -> None:
    while True:
        answers["_question_number"] = 1
        step_folder_input_path(answers)
        try:
            answers["_question_number"] = 2
            step_folder_output_location(answers)
            return
        except Back:
            continue


def run_folder_output_step_with_back(answers: dict[str, Any]) -> None:
    while True:
        try:
            answers["_question_number"] = 2
            step_folder_output_location(answers)
            return
        except Back:
            answers["_question_number"] = 1
            step_folder_input_path(answers)


def step_start_folder_now(answers: dict[str, Any]) -> None:
    cmd = build_ffmpeg_command(answers)
    answers["cmd"] = cmd
    print_summary(answers, cmd)
    answers["start_now"] = ask_yes_no(
        question_prompt(answers, "Start folder encode now?", "y/n", "y"),
        True,
    )


def run_folder_settings_wizard(answers: dict[str, Any]) -> None:
    steps = [
        Step("output_format", lambda a: True, step_output_format),
        Step("video_codec", output_has_video, step_video_codec),
        Step("use_gpu", output_has_video, step_use_gpu),
        Step("unified_video_editor", output_has_video, step_unified_video_editor_for_encode),
        Step("crop_enabled", output_has_video, step_crop_enabled),
        Step("crop_top", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_top),
        Step("crop_left", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_left),
        Step("crop_right", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_right),
        Step("crop_bottom", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_bottom),
        Step("video_bitrate", video_reencode_options_applicable, step_video_bitrate),
        Step("resolution", video_reencode_options_applicable, step_resolution),
        Step("fps", video_reencode_options_applicable, step_fps),
        Step("video_speed_reverse", output_has_video, step_video_speed_reverse_for_encode),
        Step("audio_tracks", lambda a: bool(a.get("audio_streams")), step_audio_tracks),
        Step("audio_cut", audio_only_transform_prompt_applicable, step_audio_cut_for_encode),
        Step("audio_speed_reverse", audio_only_transform_prompt_applicable, step_audio_speed_reverse_for_encode),
        Step("audio_codec", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_audio_codec),
        Step("audio_bitrate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy" and audio_codec_uses_bitrate(str(a.get("audio_codec") or default_audio_codec_for_ext(a.get("output_ext", "")))), step_audio_bitrate),
        Step("subtitle_tracks", lambda a: output_has_video(a) and bool(a.get("subtitle_streams")), step_subtitle_tracks),
        Step("start_now", lambda a: True, step_start_folder_now),
    ]

    def next_index(start: int) -> int:
        idx = start
        while idx < len(steps) and not steps[idx].applicable(answers):
            idx += 1
        return idx

    def prev_index(start: int) -> int:
        idx = start
        while idx > 0 and not steps[idx].applicable(answers):
            idx -= 1
        return max(0, idx)

    def question_number(current: int) -> int:
        count = 0
        for pos in range(current + 1):
            if steps[pos].applicable(answers):
                count += 1
        return answers.get("_question_offset", 0) + count

    idx = next_index(0)
    while idx < len(steps):
        try:
            answers["_question_number"] = question_number(idx)
            steps[idx].run(answers)
            idx = next_index(idx + 1)
        except Back:
            if idx == 0:
                raise
            idx = prev_index(idx - 1)


def run_folder_encode_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_folder_encode_mode_impl(base_answers)
    except Back:
        note("Returning to main menu.")
        return None


def _run_folder_encode_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_disable_graphical_editors"] = True
    answers["_folder_encode_mode"] = True
    answers["_quiet_packet_size_probe"] = True
    answers["_question_number"] = 1
    answers["_question_offset"] = 0

    run_folder_input_output_steps(answers)

    folder_path: Path = answers["folder_input_path"]
    output_folder: Path = answers["folder_output_location"]
    items = scan_folder_media_files(base_answers, folder_path, exclude_folder=output_folder)
    if not items:
        error("No supported audio or video files were found in this folder.")
        return None
    print_folder_media_summary(items, base_answers)
    answers["_folder_items"] = items
    note(f"Folder Encode found {len(items)} media file(s). Files will be encoded one at a time.")

    representative = choose_folder_representative(items)
    copy_media_metadata(answers, representative["answers"])
    answers["_folder_representative_path"] = representative["path"]
    answers["_question_offset"] = 2
    while True:
        try:
            run_folder_settings_wizard(answers)
            break
        except Back:
            run_folder_output_step_with_back(answers)
            folder_path = answers["folder_input_path"]
            output_folder = answers["folder_output_location"]
            items = scan_folder_media_files(base_answers, folder_path, exclude_folder=output_folder)
            if not items:
                error("No supported audio or video files were found in this folder.")
                return None
            print_folder_media_summary(items, base_answers)
            answers["_folder_items"] = items
            note(f"Folder Encode found {len(items)} media file(s). Files will be encoded one at a time.")
            representative = choose_folder_representative(items)
            copy_media_metadata(answers, representative["answers"])
            answers["_folder_representative_path"] = representative["path"]

    if not answers.get("start_now", True):
        note("FFmpeg was not started. The example command above is ready to adapt manually.")
        return None

    output_folder: Path = answers["folder_output_location"]
    try:
        output_folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        error(f"Could not create output folder: {output_folder}. {exc}")
        return 1, 0.0

    started_at = time.perf_counter()
    failures = 0
    completed = 0
    total = len(items)
    for index, item in enumerate(items, start=1):
        input_path: Path = item["path"]
        print()
        print(paint(f"Folder Encode [{index}/{total}]: {input_path.name}", Color.BOLD + Color.LIGHT_BLUE))
        try:
            job_answers = prepare_folder_job_answers(answers, item)
            cmd = build_ffmpeg_command(job_answers)
        except Exception as exc:
            failures += 1
            log_exception(f"Folder Encode could not prepare file: {input_path}")
            error(f"Skipped {input_path.name}: {exc}")
            continue

        total_duration = stream_duration_seconds({}, job_answers.get("format")) or 0.0
        log_info(
            f"Folder Encode starting {index}/{total}: input={input_path}; "
            f"output={job_answers.get('output_path')}; duration={total_duration or 'unknown'}"
        )
        print(paint("Starting FFmpeg...", Color.GREEN))
        return_code, _elapsed = run_ffmpeg_with_progress(
            cmd,
            total_duration=(total_duration if total_duration > 0 else None),
            label=f"Folder Encode {index}/{total}",
        )
        if return_code == 0:
            completed += 1
            note(f"Finished {input_path.name}")
        else:
            failures += 1
            error(f"Failed {input_path.name} with exit code {return_code}.")

    elapsed = time.perf_counter() - started_at
    print()
    if failures:
        error(f"Folder Encode completed with {completed} success(es) and {failures} failure(s).")
        return 1, elapsed
    note(f"Folder Encode completed successfully: {completed} file(s).")
    return 0, elapsed


def probe_additional_track_file(ffprobe: str, path: Path) -> dict[str, Any]:
    probe = ffprobe_json(ffprobe, path)
    streams = probe.get("streams") or []
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    if not audio_streams and not subtitle_streams:
        raise ValueError("Additional files must contain at least one audio or subtitle stream.")
    return {
        "path": path,
        "format": probe.get("format", {}),
        "audio_streams": audio_streams,
        "subtitle_streams": subtitle_streams,
        "video_streams": video_streams,
    }


def print_additional_track_file_info(item: dict[str, Any]) -> None:
    path: Path = item["path"]
    fmt = item.get("format", {})
    print()
    print(paint("Additional file info", Color.BOLD + Color.LIGHT_BLUE))
    print(paint("-" * 48, Color.GRAY))
    print(field_text("Path", path, Color.WHITE))
    print(field_text("Container", fmt.get("format_name", "unknown"), Color.CYAN))
    print(field_text("Duration", format_duration(stream_duration_seconds({}, fmt)), Color.MAGENTA))
    print(field_text("Total bitrate", describe_total_bitrate(fmt), Color.YELLOW))
    if path.exists():
        print(field_text("File size", format_bytes(path.stat().st_size), Color.LIME))

    audio_streams = item.get("audio_streams") or []
    subtitle_streams = item.get("subtitle_streams") or []
    ignored_video_streams = item.get("video_streams") or []

    if audio_streams:
        print(paint("\nAudio streams to add", Color.BOLD + Color.BLUE))
        for idx, stream in enumerate(audio_streams):
            rate = stream_bitrate_kbps(stream, fmt)
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
                f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
            f"{field_text('language', display_language(stream_tag_value(stream, 'language')), Color.WHITE)} | "
                f"{field_text('title', stream_tag_value(stream, 'title'), Color.WHITE)}"
            )

    if subtitle_streams:
        print(paint("\nSubtitle streams to add", Color.BOLD + Color.MAGENTA))
        for idx, stream in enumerate(subtitle_streams):
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('language', display_language(stream_tag_value(stream, 'language')), Color.WHITE)} | "
                f"{field_text('title', stream_tag_value(stream, 'title'), Color.WHITE)}"
            )

    if ignored_video_streams:
        print(paint("\nIgnored cover art/video streams (not added)", Color.BOLD + Color.ORANGE))
        for idx, stream in enumerate(ignored_video_streams):
            width = stream.get("width", "?")
            height = stream.get("height", "?")
            rate = stream_bitrate_kbps(stream, fmt)
            duration = format_duration(stream_duration_seconds(stream, fmt))
            disposition = stream.get("disposition") or {}
            is_cover_art = bool(disposition.get("attached_pic")) or str(stream.get("codec_name", "")).lower() in {"mjpeg", "png"}
            stream_type = "cover art" if is_cover_art else "video"
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('type', stream_type, Color.ORANGE)} | "
                f"{field_text('size', str(width) + 'x' + str(height), Color.LIME)} | "
                f"{field_text('bit depth', describe_video_bit_depth(stream), Color.PINK)} | "
                f"{field_text('Color range', display_color_range(stream.get('color_range')), Color.COLOR_RANGE_VALUE)} | "
                f"{field_text('duration', duration, Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('total bitrate', describe_total_bitrate(fmt), Color.AQUA)}"
            )
    print()


def parse_add_track_metadata(value: str) -> dict[str, str]:
    if not value or value.lower().strip() in {"n", "keep"}:
        return {}
    language, separator, title = value.partition(",")
    metadata: dict[str, str] = {}
    if language.strip():
        metadata["language"] = language.strip()
    if separator and title.strip():
        metadata["title"] = title.strip()
    return metadata


def format_track_metadata(metadata: dict[str, str]) -> str:
    if not metadata:
        return "keep existing metadata"
    parts = []
    if metadata.get("language"):
        parts.append(f"language={metadata['language']}")
    if metadata.get("title"):
        parts.append(f"title={metadata['title']}")
    return ", ".join(parts)


def ask_metadata_for_added_stream(
    answers: dict[str, Any],
    stream_kind: str,
    stream_index: int,
    stream: dict[str, Any],
) -> dict[str, str]:
    current_language = display_language(stream_tag_value(stream, "language"))
    current_title = stream_tag_value(stream, "title")
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                f"Set {stream_kind} stream {stream_index} metadata",
                (
                    f"{example_text('language,title')} {paint('like', Color.HINT_YELLOW)} {example_text('eng,English')} ; "
                    f"{keep_value_text('Enter=keep current metadata')} ; "
                    f"{field_text('current language', current_language, Color.CYAN)} ; "
                    f"{field_text('title', current_title, Color.MAGENTA)}"
                ),
                None,
                "back=0, quit=exit",
            )
        )
        if value == "0":
            raise RetryAdditionalFile()
        metadata = parse_add_track_metadata(value)
        if value and not metadata:
            error("Enter metadata like eng,English or press Enter to keep current metadata.")
            continue
        return metadata


def ask_additional_track_metadata(answers: dict[str, Any], item: dict[str, Any]) -> None:
    audio_metadata: list[dict[str, str]] = []
    subtitle_metadata: list[dict[str, str]] = []
    base_question_number = int(answers.get("_question_number", 2))

    for idx, stream in enumerate(item.get("audio_streams") or []):
        answers["_question_number"] = f"{base_question_number}.a{idx}"
        audio_metadata.append(ask_metadata_for_added_stream(answers, "audio", idx, stream))

    for idx, stream in enumerate(item.get("subtitle_streams") or []):
        answers["_question_number"] = f"{base_question_number}.s{idx}"
        subtitle_metadata.append(ask_metadata_for_added_stream(answers, "subtitle", idx, stream))

    answers["_question_number"] = base_question_number
    item["audio_metadata"] = audio_metadata
    item["subtitle_metadata"] = subtitle_metadata


def describe_additional_track_file(item: dict[str, Any]) -> str:
    parts: list[str] = []
    audio_count = len(item.get("audio_streams") or [])
    subtitle_count = len(item.get("subtitle_streams") or [])
    ignored_video_count = len(item.get("video_streams") or [])
    if audio_count:
        parts.append(f"audio streams: {audio_count}")
    if subtitle_count:
        parts.append(f"subtitle streams: {subtitle_count}")
    if ignored_video_count:
        parts.append(f"ignored cover/video streams: {ignored_video_count}")
    return " | ".join(parts) if parts else "no addable streams"


def describe_additional_track_file_colored(item: dict[str, Any]) -> str:
    parts: list[str] = []
    audio_count = len(item.get("audio_streams") or [])
    subtitle_count = len(item.get("subtitle_streams") or [])
    ignored_video_count = len(item.get("video_streams") or [])
    if audio_count:
        parts.append(field_text("audio streams", audio_count, Color.GREEN))
    if subtitle_count:
        parts.append(field_text("subtitle streams", subtitle_count, Color.MAGENTA))
    if ignored_video_count:
        parts.append(field_text("ignored cover/video streams", ignored_video_count, Color.ORANGE))
    return f" {paint('|', Color.GRAY)} ".join(parts) if parts else paint("no addable streams", Color.RED)


def add_files_supported_audio_codecs_for_container(ext: str) -> set[str] | None:
    normalized = ext.lower().lstrip(".")
    if normalized in {"mkv", "mk3d", "mka"}:
        return None
    if normalized == "webm":
        return {"opus", "vorbis"}
    if normalized in MP4_LIKE_EXTS:
        return {"aac", "mp3", "mp4a", "ac3", "eac3", "alac", "flac", "opus"}
    if normalized == "avi":
        return {"aac", "ac3", "eac3", "mp2", "mp3", "pcm_s16le", "pcm_s24le", "pcm_u8"}
    if normalized in {"ts", "m2ts", "mts"}:
        return {"aac", "ac3", "eac3", "mp2", "mp3", "dts", "truehd"}
    if normalized == "mov":
        return {"aac", "mp3", "mp4a", "ac3", "eac3", "alac", "flac", "opus", "pcm_s16le", "pcm_s24le"}
    return None


def add_files_supported_subtitle_codecs_for_container(ext: str) -> set[str] | None:
    normalized = ext.lower().lstrip(".")
    if normalized in {"mkv", "mk3d", "mka"}:
        return None
    if normalized in MP4_LIKE_EXTS:
        return {"mov_text", "tx3g"}
    if normalized == "webm":
        return {"webvtt"}
    if normalized in {"ts", "m2ts", "mts"}:
        return {"dvb_subtitle", "dvbsub", "hdmv_pgs_subtitle", "pgs"}
    return set()


def add_files_stream_copy_compatibility_errors(input_path: Path, extra_items: list[dict[str, Any]]) -> list[str]:
    ext = (input_path.suffix or ".mkv").lower().lstrip(".")
    audio_allowed = add_files_supported_audio_codecs_for_container(ext)
    subtitle_allowed = add_files_supported_subtitle_codecs_for_container(ext)
    errors: list[str] = []
    for item in extra_items:
        item_path = Path(item["path"]).name
        for stream in item.get("audio_streams") or []:
            codec = str(stream.get("codec_name") or "unknown").lower()
            if audio_allowed is not None and codec not in audio_allowed:
                errors.append(
                    f"{item_path}: audio codec {codec} cannot be safely stream-copied into .{ext}."
                )
        for stream in item.get("subtitle_streams") or []:
            codec = str(stream.get("codec_name") or "unknown").lower()
            if subtitle_allowed is not None and codec not in subtitle_allowed:
                errors.append(
                    f"{item_path}: subtitle codec {codec} cannot be safely stream-copied into .{ext}."
                )
    return errors


def choose_add_files_output_path(input_path: Path, extra_items: list[dict[str, Any]]) -> Path:
    _ = extra_items
    output_suffix = input_path.suffix or ".mkv"
    output_path = input_path.with_name(
        f"{sanitize_output_stem(input_path.stem)}{ADD_FILES_OUTPUT_SUFFIX}{output_suffix}"
    )
    return resolve_output_collision(unique_numbered_path(output_path), input_path, ADD_FILES_OUTPUT_SUFFIX)


def build_add_files_to_video_command(
    ffmpeg: str,
    input_path: Path,
    extra_items: list[dict[str, Any]],
    output_path: Path,
    source_audio_count: int = 0,
    source_subtitle_count: int = 0,
) -> list[str]:
    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n", "-i", str(input_path)]
    for item in extra_items:
        cmd.extend(["-i", str(item["path"])])
    cmd.extend(["-map", "0"])
    for input_number, item in enumerate(extra_items, start=1):
        if item.get("audio_streams"):
            cmd.extend(["-map", f"{input_number}:a?"])
        if item.get("subtitle_streams"):
            cmd.extend(["-map", f"{input_number}:s?"])
    cmd.extend(["-map_metadata", "0", "-c", "copy"])

    output_audio_index = source_audio_count
    output_subtitle_index = source_subtitle_count
    for item in extra_items:
        for metadata in item.get("audio_metadata") or [{} for _ in item.get("audio_streams", [])]:
            for key, value in metadata.items():
                cmd.extend([f"-metadata:s:a:{output_audio_index}", f"{key}={value}"])
            output_audio_index += 1
        for metadata in item.get("subtitle_metadata") or [{} for _ in item.get("subtitle_streams", [])]:
            for key, value in metadata.items():
                cmd.extend([f"-metadata:s:s:{output_subtitle_index}", f"{key}={value}"])
            output_subtitle_index += 1

    cmd.append(str(output_path))
    return cmd


def ask_add_files_source_video(answers: dict[str, Any]) -> None:
    while True:
        video_example = example_text('"E:\\Input\\video.mkv"')
        value = ask_required(
            question_prompt(
                answers,
                "Enter source video file path",
                f"drag and drop a video file here or paste a path; example: {video_example}",
            )
        )
        input_path = terminal_path(value)
        if not input_path.exists() or not input_path.is_file():
            error("File not found. Enter the full file path again.")
            continue
        try:
            load_input_metadata(answers, input_path)
        except FFprobeError as exc:
            error(str(exc))
            continue
        except Exception:
            log_exception(f"ffprobe metadata load failed for Add files source video: {input_path}")
            error(f"ffprobe could not read the file. See log file: {_log_file_text()}")
            continue
        if not answers.get("video_streams"):
            error("The first input must contain a video stream.")
            continue
        print_source_info(answers)
        return


def ask_additional_track_files(
    answers: dict[str, Any],
    current_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    extra_items: list[dict[str, Any]] = list(current_items or [])
    while True:
        answers["_question_number"] = len(extra_items) + 2
        value = ask_raw(
            question_prompt(
                answers,
                "Enter audio/subtitle file to add",
                (
                    f"{paint('type done when finished', Color.LIME)}; "
                    f"{paint('audio', Color.BLUE)} or {paint('subtitle', Color.MAGENTA)} file; "
                    f"{paint('Enter after each path asks for the next file', Color.HINT_YELLOW)}"
                ),
            )
        )
        lowered = value.lower().strip()
        if lowered == "0":
            if extra_items:
                removed = extra_items.pop()
                note(f"Removed added file: {Path(removed['path']).name}")
                continue
            raise Back()
        if lowered == "done":
            if not extra_items:
                error("Add at least one audio or subtitle file before typing done.")
                continue
            return extra_items
        if not value:
            error("Enter an audio/subtitle file path, or type done when finished.")
            continue

        path = terminal_path(value)
        if not path.exists() or not path.is_file():
            error("File not found. Enter the full file path again.")
            continue
        if paths_same(path, answers["input_path"]):
            error("The additional file cannot be the same as the source video.")
            continue
        try:
            item = probe_additional_track_file(answers["ffprobe"], path)
        except FFprobeError as exc:
            error(str(exc))
            continue
        except Exception as exc:
            log_exception(f"Could not probe additional track file: {path}")
            error(str(exc))
            continue
        print_additional_track_file_info(item)
        try:
            ask_additional_track_metadata(answers, item)
        except RetryAdditionalFile:
            continue
        extra_items.append(item)
        note(f"Added file #{len(extra_items)}: {path.name} ({describe_additional_track_file(item)})")


def print_add_files_summary(
    answers: dict[str, Any],
    extra_items: list[dict[str, Any]],
    output_path: Path,
    cmd: list[str],
) -> None:
    print()
    print(paint("Add files to video summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("Input video", answers["input_path"], Color.WHITE))
    print("  " + field_text("Output", output_path, Color.LIME))
    print(paint("  Files to add:", Color.BOLD + Color.LIGHT_BLUE))
    for index, item in enumerate(extra_items, start=1):
        print(
            f"    {paint(str(index) + '.', Color.LIGHT_BLUE)} "
            f"{paint(str(item['path']), Color.WHITE)} | "
            f"{describe_additional_track_file_colored(item)}"
        )
        for audio_index, metadata in enumerate(item.get("audio_metadata") or []):
            print(
                f"       {paint('audio ' + str(audio_index), Color.BLUE)} "
                f"{paint(format_track_metadata(metadata), Color.WHITE)}"
            )
        for subtitle_index, metadata in enumerate(item.get("subtitle_metadata") or []):
            print(
                f"       {paint('subtitle ' + str(subtitle_index), Color.MAGENTA)} "
                f"{paint(format_track_metadata(metadata), Color.WHITE)}"
            )
    print()
    print(paint("Final PowerShell command:", Color.FINAL_COMMAND_LABEL))
    log_info("Final PowerShell command: " + command_to_powershell(cmd))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))


def run_add_files_to_video_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_add_files_to_video_mode_impl(base_answers)
    except Back:
        note("Returning to main menu.")
        return None


def _run_add_files_to_video_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    ask_add_files_source_video(answers)
    extra_items: list[dict[str, Any]] = []
    while True:
        try:
            extra_items = ask_additional_track_files(answers, extra_items)
        except Back:
            answers["_question_number"] = 1
            ask_add_files_source_video(answers)
            extra_items = []
            continue

        compatibility_errors = add_files_stream_copy_compatibility_errors(answers["input_path"], extra_items)
        if compatibility_errors:
            error("Cannot add these streams without changing container or re-encoding:")
            for message in compatibility_errors:
                error(f"  {message}")
            error("Add files mode keeps the original container and uses stream copy only. No output was created.")
            return None

        output_path = choose_add_files_output_path(answers["input_path"], extra_items)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = build_add_files_to_video_command(
            answers["ffmpeg"],
            answers["input_path"],
            extra_items,
            output_path,
            source_audio_count=len(answers.get("audio_streams") or []),
            source_subtitle_count=len(answers.get("subtitle_streams") or []),
        )
        print_add_files_summary(answers, extra_items, output_path, cmd)
        answers["_question_number"] = len(extra_items) + 3
        try:
            start_now = ask_yes_no(
                question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
                True,
            )
        except Back:
            continue
        if not start_now:
            note("FFmpeg was not started. The command above is ready to run manually.")
            return None
        break

    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    log_info(
        f"Add files to video starting: input={answers['input_path']}; "
        f"output={output_path}; extra_files={len(extra_items)}"
    )
    return run_ffmpeg_with_progress(
        cmd,
        total_duration=(duration if duration > 0 else None),
        label="Add files to video",
    )


def source_video_codec_family(answers: dict[str, Any]) -> str:
    codec = str((answers.get("video_streams") or [{}])[0].get("codec_name", "")).lower()
    if codec in {"h264", "avc1"}:
        return "H264"
    if codec in {"hevc", "h265"}:
        return "H265"
    if codec == "av1":
        return "AV1"
    if codec == "vp9":
        return "VP9"
    return "H265"


def video_hdr_dolby_info(stream: dict[str, Any]) -> dict[str, Any]:
    side_data = stream.get("side_data_list") or []
    side_text = json.dumps(side_data, ensure_ascii=False).lower()
    tags_text = json.dumps(stream.get("tags") or {}, ensure_ascii=False).lower()
    color_transfer = str(stream.get("color_transfer") or "").lower()
    color_primaries = str(stream.get("color_primaries") or "").lower()
    color_space = str(stream.get("color_space") or "").lower()
    hdr = (
        color_transfer in {"smpte2084", "arib-std-b67"}
        or color_primaries == "bt2020"
        or color_space.startswith("bt2020")
        or "mastering display metadata" in side_text
        or "content light level metadata" in side_text
    )
    dolby = (
        "dovi" in side_text
        or "dolby vision" in side_text
        or "dv_profile" in side_text
        or "dovi" in tags_text
        or "dolby vision" in tags_text
    )
    return {
        "hdr": hdr,
        "dolby": dolby,
        "color_transfer": stream.get("color_transfer", "unknown"),
        "color_primaries": stream.get("color_primaries", "unknown"),
        "color_space": stream.get("color_space", "unknown"),
        "color_range": stream.get("color_range", "unknown"),
        "bit_depth": describe_video_bit_depth(stream),
    }


def hardsub_filter_quote_path(path: Path) -> str:
    text = path.resolve().as_posix()
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    text = text.replace(",", "\\,")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    return "'" + text + "'"


def hardsub_internal_subtitle_codec_is_supported(codec: str) -> bool:
    return str(codec or "").strip().lower() in TEXT_SUBTITLE_CODECS


def hardsub_internal_subtitle_error(codec: str) -> str | None:
    normalized = str(codec or "").strip().lower()
    if normalized in TEXT_SUBTITLE_CODECS:
        return None
    if normalized in BITMAP_SUBTITLE_CODECS:
        return HARDSUB_BITMAP_SUBTITLE_ERROR
    return (
        f"Subtitle codec {normalized or 'unknown'} is not supported by this HardSub mode. "
        "Choose a text subtitle stream or use an external .srt/.ass/.ssa/.vtt/.webvtt file."
    )


def hardsub_external_subtitle_extension_supported(path: Path) -> bool:
    return path.suffix.lower() in HARDSUB_SUBTITLE_EXTS


def hardsub_input_output_exts(answers: dict[str, Any]) -> tuple[str, str]:
    input_path: Path = answers["input_path"]
    input_ext = input_path.suffix.lstrip(".").lower() or "mkv"
    output_ext = str(answers.get("output_ext") or input_ext).lower().lstrip(".")
    return input_ext, output_ext


def ass_ssa_has_embedded_fonts(path: Path) -> bool:
    if path.suffix.lower() not in {".ass", ".ssa"}:
        return False
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        log_exception(f"Could not scan ASS/SSA embedded fonts: {path}")
        return False
    in_fonts_section = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_fonts_section = line.lower() == "[fonts]"
            continue
        if in_fonts_section and line.lower().startswith("fontname:") and line.split(":", 1)[1].strip():
            return True
    return False


def hardsub_subtitle_filter(answers: dict[str, Any]) -> str:
    source = answers.get("hardsub_subtitle_source")
    parts: list[str]
    if source == "internal":
        input_path: Path = answers["input_path"]
        subtitle_index = int(answers.get("hardsub_subtitle_index", 0))
        parts = [f"filename={hardsub_filter_quote_path(input_path)}", f"si={subtitle_index}"]
    else:
        subtitle_path: Path = answers["hardsub_subtitle_path"]
        parts = [f"filename={hardsub_filter_quote_path(subtitle_path)}"]
    fontsdir = answers.get("hardsub_fontsdir")
    if fontsdir:
        parts.append(f"fontsdir={hardsub_filter_quote_path(Path(fontsdir))}")
    return "subtitles=" + ":".join(parts)


def hardsub_output_10bit(answers: dict[str, Any]) -> bool:
    stream = (answers.get("video_streams") or [{}])[0]
    hdr_info = answers.get("hardsub_hdr_info") or video_hdr_dolby_info(stream)
    return (video_bit_depth(stream) or 8) > 8 or bool(hdr_info.get("hdr") or hdr_info.get("dolby"))


def build_hardsub_video_filter(answers: dict[str, Any], video_encoder: str) -> str:
    subtitle_filter = hardsub_subtitle_filter(answers)
    handling = answers.get("hardsub_hdr_handling", "standard")
    filters: list[str] = []
    if handling == "tone-map":
        filters.extend([
            "zscale=t=linear:npl=100",
            "format=gbrpf32le",
            "zscale=p=bt709:t=bt709:m=bt709:r=tv",
            "tonemap=hable:desat=0",
            "format=yuv420p",
            subtitle_filter,
            "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        ])
    else:
        filters.append(subtitle_filter)
        if hardsub_output_10bit(answers):
            filters.append("format=p010le" if video_encoder.endswith("_nvenc") else "format=yuv420p10le")
        else:
            filters.append("format=yuv420p")
        source_range = str((answers.get("video_streams") or [{}])[0].get("color_range") or "").lower()
        if source_range in {"tv", "pc"}:
            filters.append(f"setparams=range={source_range}")
    if FORCE_SAR:
        filters.append(f"setsar={FORCE_SAR}")
    return ",".join(filters)


def hardsub_quality_value(answers: dict[str, Any], video_encoder: str) -> int:
    if answers.get("hardsub_quality_mode") == "custom":
        return int(answers.get("hardsub_quality_value", 18))
    mode = answers.get("hardsub_quality_mode", "near-lossless")
    preset = HARDSUB_QUALITY_PRESETS.get(mode, HARDSUB_QUALITY_PRESETS["near-lossless"])
    if video_encoder.endswith("_nvenc"):
        return preset["nvenc"]
    if video_encoder in {"libx265", "libaom-av1", "libvpx-vp9"}:
        return preset["cpu_hevc"]
    return preset["cpu"]


def append_hardsub_quality_args(cmd: list[str], answers: dict[str, Any], video_encoder: str) -> None:
    quality = hardsub_quality_value(answers, video_encoder)
    if video_encoder.endswith("_nvenc"):
        cmd.extend(["-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq:v", str(quality), "-b:v", "0"])
    elif video_encoder in {"libx264", "libx265"}:
        cmd.extend(["-preset", "slow", "-crf", str(quality)])
    elif video_encoder in {"libaom-av1", "libvpx-vp9"}:
        cmd.extend(["-crf", str(quality), "-b:v", "0"])
    else:
        cmd.extend(["-q:v", str(max(1, min(31, quality)))])


def append_hardsub_color_args(cmd: list[str], answers: dict[str, Any]) -> None:
    handling = answers.get("hardsub_hdr_handling", "standard")
    stream = (answers.get("video_streams") or [{}])[0]
    if handling == "tone-map":
        cmd.extend(["-color_range", "tv", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"])
        return
    if handling != "preserve":
        return
    for ff_arg, key in (
        ("-color_range", "color_range"),
        ("-color_primaries", "color_primaries"),
        ("-color_trc", "color_transfer"),
        ("-colorspace", "color_space"),
    ):
        value = str(stream.get(key) or "").strip()
        if value and value.lower() not in {"unknown", "unspecified", "reserved"}:
            cmd.extend([ff_arg, value])


def choose_hardsub_output_path(input_path: Path, output_ext: str, output_location: Path, output_name_stem: str | None = None) -> Path:
    if output_name_stem:
        candidate = output_location / f"{sanitize_output_stem(output_name_stem)}.{output_ext}"
    elif output_location.suffix:
        candidate = output_location.with_suffix("." + output_ext)
    else:
        candidate = output_location / f"{sanitize_output_stem(input_path.stem)}{HARDSUB_OUTPUT_SUFFIX}.{output_ext}"
    return resolve_output_collision(unique_numbered_path(candidate), input_path, HARDSUB_OUTPUT_SUFFIX)


def build_hardsub_command(answers: dict[str, Any]) -> list[str]:
    answers["_hardsub_mode"] = True
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    input_ext, output_ext = hardsub_input_output_exts(answers)
    output_path = choose_hardsub_output_path(
        input_path,
        output_ext,
        answers["output_location"],
        answers.get("output_name_stem"),
    )
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    video_encoder, tag, _profile = resolve_video_encoder(answers)
    if video_encoder == "copy":
        video_encoder = "libx265"
    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n", "-i", str(input_path)]

    cmd.extend(["-map", "0:v:0"])
    audio_mode = answers.get("hardsub_audio_mode", "copy-all")
    audio_policy = answers.get("hardsub_audio_container_policy")
    if audio_mode != "none" and input_ext != output_ext and not audio_policy:
        raise ValueError("HardSub audio container policy is required when output container differs from the source container.")
    if audio_policy == "match-source-container":
        audio_policy = "copy-anyway"
    if input_ext == output_ext and not audio_policy:
        audio_policy = "copy-anyway"
    if audio_mode == "none" or audio_policy == "none":
        cmd.append("-an")
    elif audio_mode == "selected":
        for index in answers.get("hardsub_audio_tracks", []):
            cmd.extend(["-map", f"0:a:{index}"])
        if audio_policy == "aac":
            bitrate = int(answers.get("hardsub_audio_bitrate_kbps") or DEFAULT_AUDIO_BITRATE_KBPS)
            cmd.extend(["-c:a", "aac", "-b:a", f"{bitrate}k", "-ac", "2"])
        else:
            cmd.extend(["-c:a", "copy"])
    else:
        cmd.extend(["-map", "0:a?"])
        if audio_policy == "aac":
            bitrate = int(answers.get("hardsub_audio_bitrate_kbps") or DEFAULT_AUDIO_BITRATE_KBPS)
            cmd.extend(["-c:a", "aac", "-b:a", f"{bitrate}k", "-ac", "2"])
        else:
            cmd.extend(["-c:a", "copy"])

    cmd.extend(["-sn", "-dn", "-map_metadata", "0", "-map_chapters", "0"])
    log_info("Hard Sub Encode uses the CPU subtitles/libass filter chain; NVENC may still be used for video encode.")
    cmd.extend(["-filter:v", build_hardsub_video_filter(answers, video_encoder)])
    cmd.extend(["-c:v", video_encoder])
    append_hardsub_quality_args(cmd, answers, video_encoder)
    append_hardsub_color_args(cmd, answers)
    if video_encoder == "hevc_nvenc" and hardsub_output_10bit(answers):
        cmd.extend(["-profile:v", "main10"])
    elif video_encoder == "hevc_nvenc":
        cmd.extend(["-profile:v", "main"])
    if tag and output_ext in MP4_LIKE_EXTS:
        cmd.extend(["-tag:v", tag])
    if output_ext in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])
    cmd.append(str(output_path))
    return cmd


def ask_hardsub_source_video(answers: dict[str, Any]) -> None:
    while True:
        video_example = example_text(r"E:\Input\video.mkv")
        value = ask_required(
            question_prompt(
                answers,
                "Enter source video file path",
                f"drag and drop a video file here or paste a path; example: {video_example}",
            )
        )
        input_path = terminal_path(value)
        if not input_path.exists() or not input_path.is_file():
            error("File not found. Enter the full file path again.")
            continue
        try:
            load_input_metadata(answers, input_path)
        except FFprobeError as exc:
            error(str(exc))
            continue
        except Exception:
            log_exception(f"ffprobe metadata load failed for Hard Sub source video: {input_path}")
            error(f"ffprobe could not read the file. See log file: {_log_file_text()}")
            continue
        if not answers.get("video_streams"):
            error("The input must contain a video stream.")
            continue
        answers["hardsub_hdr_info"] = video_hdr_dolby_info(answers["video_streams"][0])
        print_source_info(answers)
        return


def step_hardsub_output_location(answers: dict[str, Any]) -> None:
    folder_example = example_text(r"E:\output")
    value = ask_raw(
        question_prompt(
            answers,
            "Enter output path, output folder, or bare output name",
            f"Enter=same folder as input; default suffix {HARDSUB_OUTPUT_SUFFIX}; example: {folder_example}",
        )
    )
    if value == "0":
        raise Back()
    apply_output_location_value(answers, value)


def step_hardsub_output_format(answers: dict[str, Any]) -> None:
    input_ext = answers["input_path"].suffix.lstrip(".") or "mkv"
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Enter final output format",
                f"common: {option_list(COMMON_VIDEO_FORMATS)}; {keep_value_text(f'n=Use input format ({input_ext})')}",
                "n",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = "n"
        try:
            answers["output_ext"] = normalize_format(value, input_ext)
            return
        except ValueError as exc:
            error(str(exc))


def step_hardsub_subtitle_source(answers: dict[str, Any]) -> None:
    subtitle_streams = answers.get("subtitle_streams") or []
    if subtitle_streams:
        print()
        print(paint("Internal subtitle streams", Color.BOLD + Color.MAGENTA))
        for index, stream in enumerate(subtitle_streams, start=1):
            print(
                f"  {paint(str(index) + '.', Color.LIGHT_BLUE)} "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('language', display_language(stream_tag_value(stream, 'language')), Color.WHITE)} | "
                f"{field_text('title', stream_tag_value(stream, 'title'), Color.WHITE)}"
            )
        while True:
            value = ask_raw(
                question_prompt(
                    answers,
                    "Choose subtitle source",
                    "1=internal subtitle track; 2=external .srt/.ass/.ssa/.vtt/.webvtt file",
                    "1",
                )
            )
            if value == "0":
                raise Back()
            if not value:
                value = "1"
            if value in {"1", "2"}:
                source = "internal" if value == "1" else "external"
                break
            error("Enter 1 or 2.")
    else:
        note("No internal subtitle streams were found; external subtitle file will be used.")
        source = "external"

    if source == "internal":
        while True:
            value = ask_raw(
                question_prompt(
                    answers,
                    "Choose internal subtitle track",
                    f"1-{len(subtitle_streams)}; track number 1 is the first subtitle stream",
                    "1",
                )
            )
            if value == "0":
                raise Back()
            if not value:
                value = "1"
            if re.fullmatch(r"\d+", value) and 1 <= int(value) <= len(subtitle_streams):
                selected = int(value) - 1
                codec = str(subtitle_streams[selected].get("codec_name", "")).lower()
                codec_error = hardsub_internal_subtitle_error(codec)
                if codec_error:
                    error(codec_error)
                    continue
                answers["hardsub_subtitle_source"] = "internal"
                answers["hardsub_subtitle_index"] = selected
                answers["hardsub_subtitle_codec"] = codec
                return
            error(f"Enter a number from 1 to {len(subtitle_streams)}.")

    while True:
        value = ask_required(
            question_prompt(
                answers,
                "Enter external subtitle file path",
                f"supported common files: {option_list(sorted(ext.lstrip('.') for ext in HARDSUB_SUBTITLE_EXTS))}",
            )
        )
        path = terminal_path(value)
        if not path.exists() or not path.is_file():
            error("Subtitle file not found. Enter the full path again.")
            continue
        if not hardsub_external_subtitle_extension_supported(path):
            error("Unsupported external subtitle extension. Use .srt, .ass, .ssa, .vtt, or .webvtt.")
            continue
        if path.suffix.lower() in {".ass", ".ssa"}:
            if ass_ssa_has_embedded_fonts(path):
                note("This ASS/SSA file contains embedded fonts. fontsdir is optional.")
            else:
                note("No embedded fonts were found. If this subtitle uses custom fonts that are not installed system-wide, provide a fonts directory.")
        answers["hardsub_subtitle_source"] = "external"
        answers["hardsub_subtitle_path"] = path
        return


def step_hardsub_fontsdir(answers: dict[str, Any]) -> None:
    fonts_example = example_text(r"D:\Subs\fonts")
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Enter subtitle fonts directory",
                f"Enter=none; optional for ASS/SSA embedded fonts and MKV font attachments; example: {fonts_example}",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            answers["hardsub_fontsdir"] = None
            return
        path = terminal_path(value)
        if not path.exists() or not path.is_dir():
            error("Fonts directory not found. Enter an existing folder path or press Enter for none.")
            continue
        answers["hardsub_fontsdir"] = path
        return


def step_hardsub_video_codec(answers: dict[str, Any]) -> None:
    hdr_info = answers.get("hardsub_hdr_info") or {}
    default_codec = "H265" if hdr_info.get("hdr") or hdr_info.get("dolby") else source_video_codec_family(answers)
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Enter hard-sub video codec",
                f"common: {option_list(['H265', 'H264', 'AV1', 'VP9'])}; {keep_value_text(f'n=match source family ({default_codec})')}; copy is not possible for hard subtitles",
                "n",
            )
        )
        if value == "0":
            raise Back()
        if not value or value.lower() == "n":
            value = default_codec
        if value.lower() == "copy":
            error("Hard subtitles require video re-encoding; copy is not valid here.")
            continue
        answers["video_codec"] = value
        return


def step_hardsub_use_gpu(answers: dict[str, Any]) -> None:
    answers["use_gpu"] = ask_yes_no(
        question_prompt(answers, "Use GPU/NVIDIA encoder if available?", "y/n", "y"),
        True,
    )


def step_hardsub_quality(answers: dict[str, Any]) -> None:
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Choose hard-sub quality",
                colored_hardsub_quality_options(),
                "1",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = "1"
        mapping = {"1": "near-lossless", "2": "high quality", "3": "balanced"}
        if value in mapping:
            answers["hardsub_quality_mode"] = mapping[value]
            return
        if value == "4":
            while True:
                custom = ask_raw(
                    question_prompt(
                        answers,
                        "Enter custom quality value",
                        "CRF/CQ integer 1-51; lower is higher quality",
                        "18",
                    )
                )
                if custom == "0":
                    raise Back()
                if not custom:
                    custom = "18"
                if re.fullmatch(r"\d+", custom) and 1 <= int(custom) <= 51:
                    answers["hardsub_quality_mode"] = "custom"
                    answers["hardsub_quality_value"] = int(custom)
                    return
                error("Enter an integer from 1 to 51.")
        else:
            error("Enter 1, 2, 3, or 4.")


def step_hardsub_hdr_handling(answers: dict[str, Any]) -> None:
    hdr_info = answers.get("hardsub_hdr_info") or {}
    if not hdr_info.get("hdr") and not hdr_info.get("dolby"):
        answers["hardsub_hdr_handling"] = "standard"
        return
    print()
    print(paint("HDR / Dolby Vision detected", Color.BOLD + Color.ORANGE))
    print("  " + field_text("HDR", "yes" if hdr_info.get("hdr") else "no", Color.ORANGE))
    print("  " + field_text("Dolby Vision", "yes" if hdr_info.get("dolby") else "no", Color.ORANGE))
    print("  " + field_text("transfer", hdr_info.get("color_transfer"), Color.CYAN))
    print("  " + field_text("primaries", hdr_info.get("color_primaries"), Color.CYAN))
    print("  " + field_text("bit depth", hdr_info.get("bit_depth"), Color.PINK))
    if hdr_info.get("dolby"):
        note("Dolby Vision dynamic metadata cannot be reliably preserved after hard-sub re-encoding; HDR10/static metadata can only be copied best-effort.")
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Choose HDR/Dolby handling",
                "1=preserve HDR metadata best effort; 2=tone-map to SDR; 3=standard encode without HDR-specific handling",
                "1",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = "1"
        mapping = {"1": "preserve", "2": "tone-map", "3": "standard"}
        if value in mapping:
            answers["hardsub_hdr_handling"] = mapping[value]
            return
        error("Enter 1, 2, or 3.")


def step_hardsub_audio_mode(answers: dict[str, Any]) -> None:
    if not answers.get("audio_streams"):
        answers["hardsub_audio_mode"] = "none"
        return
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Choose audio handling",
                colored_hardsub_audio_options(),
                "1",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = "1"
        if value == "1":
            answers["hardsub_audio_mode"] = "copy-all"
            return
        if value == "3":
            answers["hardsub_audio_mode"] = "none"
            return
        if value == "2":
            answers["hardsub_audio_mode"] = "selected"
            answers["hardsub_audio_tracks"] = ask_selection(
                question_prompt(
                    answers,
                    "Which audio tracks should be copied?",
                    f"example: {example_text('0,1')}; 0 is the first audio track here; back=b, quit=exit",
                    back="back=b, quit=exit",
                ),
                max_count=len(answers["audio_streams"]),
                default=[0],
                allow_none=True,
            )
            if answers["hardsub_audio_tracks"] == "all":
                answers["hardsub_audio_mode"] = "copy-all"
                answers.pop("hardsub_audio_tracks", None)
            return
        error("Enter 1, 2, or 3.")


def step_hardsub_audio_container_policy(answers: dict[str, Any]) -> None:
    if answers.get("hardsub_audio_mode") == "none":
        answers["hardsub_audio_container_policy"] = "none"
        return
    input_ext, output_ext = hardsub_input_output_exts(answers)
    if input_ext == output_ext:
        answers["hardsub_audio_container_policy"] = "copy-anyway"
        return
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Choose audio handling for different output container",
                (
                    "1=copy audio anyway; 2=transcode selected/copied audio to AAC stereo; "
                    "3=change output format to match source container; 4=no audio"
                ),
                "2",
            )
        )
        if value == "0":
            raise Back()
        if not value:
            value = "2"
        if value == "1":
            note("Audio copy across different containers may fail if the codec is not supported by the output container.")
            answers["hardsub_audio_container_policy"] = "copy-anyway"
            return
        if value == "2":
            answers["hardsub_audio_container_policy"] = "aac"
            answers["hardsub_audio_bitrate_kbps"] = DEFAULT_AUDIO_BITRATE_KBPS
            return
        if value == "3":
            answers["output_ext"] = input_ext
            answers["hardsub_audio_container_policy"] = "match-source-container"
            note(f"HardSub output format changed to match the source container: {input_ext}")
            return
        if value == "4":
            answers["hardsub_audio_container_policy"] = "none"
            return
        error("Enter 1, 2, 3, or 4.")


def step_hardsub_start_now(answers: dict[str, Any]) -> None:
    cmd = build_hardsub_command(answers)
    answers["cmd"] = cmd
    print()
    print(paint("Hard Sub Encode summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("input", answers["input_path"], Color.WHITE))
    if answers.get("hardsub_subtitle_source") == "internal":
        print("  " + field_text("subtitle", f"internal subtitle #{answers.get('hardsub_subtitle_index')}", Color.MAGENTA))
    else:
        print("  " + field_text("subtitle", answers.get("hardsub_subtitle_path"), Color.MAGENTA))
    print("  " + field_text("output", answers["output_path"], Color.LIME))
    print("  " + field_text("video codec", answers.get("video_codec"), Color.CYAN))
    print("  " + field_text("quality", answers.get("hardsub_quality_mode"), Color.YELLOW))
    print("  " + field_text("HDR/Dolby handling", answers.get("hardsub_hdr_handling"), Color.ORANGE))
    print("  " + field_text("audio", answers.get("hardsub_audio_mode"), Color.BLUE))
    print("  " + field_text("audio container policy", answers.get("hardsub_audio_container_policy", "copy-anyway"), Color.CYAN))
    print()
    print(paint("Final PowerShell command:", Color.FINAL_COMMAND_LABEL))
    log_info("Final PowerShell command: " + command_to_powershell(cmd))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    answers["start_now"] = ask_yes_no(
        question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def run_hardsub_encode_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_hardsub_encode_mode_impl(base_answers)
    except Back:
        note("Returning to main menu.")
        return None


def _run_hardsub_encode_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    steps = [
        Step("input_path", lambda a: True, ask_hardsub_source_video),
        Step("output_location", lambda a: True, step_hardsub_output_location),
        Step("output_format", lambda a: True, step_hardsub_output_format),
        Step("hardsub_subtitle", lambda a: True, step_hardsub_subtitle_source),
        Step("hardsub_fontsdir", lambda a: True, step_hardsub_fontsdir),
        Step("video_codec", lambda a: True, step_hardsub_video_codec),
        Step("use_gpu", lambda a: True, step_hardsub_use_gpu),
        Step("hardsub_quality", lambda a: True, step_hardsub_quality),
        Step("hardsub_hdr", lambda a: True, step_hardsub_hdr_handling),
        Step("hardsub_audio", lambda a: True, step_hardsub_audio_mode),
        Step("hardsub_audio_container", lambda a: True, step_hardsub_audio_container_policy),
        Step("start_now", lambda a: True, step_hardsub_start_now),
    ]

    idx = 0
    while idx < len(steps):
        try:
            answers["_question_number"] = idx + 1
            steps[idx].run(answers)
            idx += 1
        except Back:
            if idx == 0:
                raise
            idx -= 1

    if not answers.get("start_now", True):
        note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    log_info(
        f"Hard Sub Encode starting: input={answers['input_path']}; "
        f"output={answers.get('output_path')}; subtitle_source={answers.get('hardsub_subtitle_source')}"
    )
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(duration if duration > 0 else None),
        label="Hard Sub Encode",
    )


def build_cut_filter_complex(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    audio_for_cut: int | None,
) -> str:
    """Build the -filter_complex argument for the wizard re-encode cut path."""
    if not keep_ranges:
        raise ValueError("build_cut_filter_complex requires at least one keep range.")
    fc_parts: list[str] = []
    for idx, (start, end) in enumerate(keep_ranges):
        fc_parts.append(
            f"[0:v:0]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[v{idx}]"
        )
        if audio_for_cut is not None:
            fc_parts.append(
                f"[0:a:{audio_for_cut}]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS[a{idx}]"
            )

    if len(keep_ranges) > 1:
        concat_inputs = ""
        for idx in range(len(keep_ranges)):
            concat_inputs += f"[v{idx}]"
            if audio_for_cut is not None:
                concat_inputs += f"[a{idx}]"
        if audio_for_cut is not None:
            if audio_speed_transform_enabled(answers):
                fc_parts.append(f"{concat_inputs}concat=n={len(keep_ranges)}:v=1:a=1[vc][ac]")
                fc_parts.append(f"[ac]{build_encode_audio_speed_filter(answers)}[a]")
            else:
                fc_parts.append(f"{concat_inputs}concat=n={len(keep_ranges)}:v=1:a=1[vc][a]")
        else:
            fc_parts.append(f"{concat_inputs}concat=n={len(keep_ranges)}:v=1:a=0[vc]")
        video_label = "vc"
    else:
        video_label = "v0"
        if audio_for_cut is not None:
            fc_parts.append("[a0]asetpts=PTS-STARTPTS[a]")

    # Apply the user's video filters (crop/fps/scale/setsar/setparams) after concat.
    user_video_filter = build_cpu_video_filter(answers)
    if user_video_filter:
        fc_parts.append(f"[{video_label}]{user_video_filter}[v]")
    else:
        fc_parts.append(f"[{video_label}]null[v]")
    return ";".join(fc_parts)


def print_startup_banner(config_path: Path, launcher_path: Path) -> None:
    _ = (config_path, launcher_path)
    startup_line("FFmpeg", "found.", Color.LIME)


def run_video_speed_reverse_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_video_speed_reverse_mode_impl(base_answers)
    except Back:
        note("Returning to main menu.")
        return None


def _run_video_speed_reverse_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, step_input_path),
        Step("speed_reverse", lambda a: True, step_video_speed_reverse_options),
        Step("output_location", lambda a: not a.get("_speed_reverse_noop"), step_output_location),
        Step("output_format", lambda a: not a.get("_speed_reverse_noop"), step_output_format),
        Step("start_now", lambda a: not a.get("_speed_reverse_noop"), step_video_speed_start_now),
    ]
    while True:
        try:
            run_mode_steps(answers, steps)
            break
        except ValueError as exc:
            error(str(exc))
            return None
    ensure_video_input(answers)
    if answers.get("_speed_reverse_noop"):
        note("Video speed/reverse was not enabled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    if answers.get("reverse_video"):
        return run_segmented_reverse_video_speed(answers)
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(duration / max(0.001, float(answers.get("speed_factor", 1.0))) if duration > 0 else None),
        label="Video Speed / Reverse",
    )


def run_audio_cut_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_audio_cut_mode_impl(base_answers)
    except Back:
        note("Returning to main menu.")
        return None


def _run_audio_cut_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, step_input_path),
        Step("audio_track", lambda a: True, step_audio_track_for_tool),
        Step("output_location", lambda a: True, step_output_location),
        Step("audio_cut_gui", lambda a: True, step_audio_cut_editor),
        Step("start_now", lambda a: not a.get("_audio_cut_noop"), step_audio_cut_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except ValueError as exc:
        error(str(exc))
        return None
    ensure_audio_input(answers)
    if answers.get("_audio_cut_noop"):
        note("Audio cut was canceled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    total_duration = total_keep_duration(answers.get("audio_keep_ranges") or [])
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(total_duration if total_duration > 0 else None),
        label="Audio Cut",
    )


def run_audio_speed_reverse_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_audio_speed_reverse_mode_impl(base_answers)
    except Back:
        note("Returning to main menu.")
        return None


def _run_audio_speed_reverse_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, step_input_path),
        Step("audio_track", lambda a: True, step_audio_track_for_tool),
        Step("speed_reverse", lambda a: True, step_audio_speed_reverse_options),
        Step("output_location", lambda a: not a.get("_speed_reverse_noop"), step_output_location),
        Step("start_now", lambda a: not a.get("_speed_reverse_noop"), step_audio_speed_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except ValueError as exc:
        error(str(exc))
        return None
    ensure_audio_input(answers)
    if answers.get("_speed_reverse_noop"):
        note("Audio speed/reverse was not enabled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(duration / max(0.001, float(answers.get("speed_factor", 1.0))) if duration > 0 else None),
        label="Audio Speed / Reverse",
    )


def run_audio_transform_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_audio_transform_mode_impl(base_answers)
    except Back:
        note("Returning to main menu.")
        return None


def _run_audio_transform_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, step_input_path),
        Step("audio_track", lambda a: True, step_audio_track_for_tool),
        Step("output_location", lambda a: True, step_output_location),
        Step("audio_transform_gui", lambda a: True, step_audio_transform_editor),
        Step("start_now", lambda a: not a.get("_audio_transform_noop"), step_audio_transform_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except ValueError as exc:
        error(str(exc))
        return None
    ensure_audio_input(answers)
    if answers.get("_audio_transform_noop"):
        note("Audio transform was not enabled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_duration = total_keep_duration(answers.get("audio_cut_keep_ranges") or [])
    if keep_duration <= 0:
        keep_duration = duration
    speed = max(0.001, float(answers.get("audio_speed_factor", DEFAULT_SPEED_FACTOR) or DEFAULT_SPEED_FACTOR))
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(keep_duration / speed if keep_duration > 0 else None),
        label="Audio Cut / Speed / Reverse",
    )


def run_one_job(base_answers: dict[str, Any], config_path: Path) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    start_mode = ask_main_menu(answers, config_path)
    if start_mode == 10:
        return run_audio_transform_mode(base_answers)
    if start_mode == 9:
        return run_video_speed_reverse_mode(base_answers)
    if start_mode == 8:
        return run_hardsub_encode_mode(base_answers)
    if start_mode == 7:
        return run_mux_cleanup_mode(base_answers)
    if start_mode == 6:
        run_media_info_mode(base_answers)
        return None
    if start_mode == 5:
        return run_add_files_to_video_mode(base_answers)
    if start_mode == 4:
        return run_folder_encode_mode(base_answers)
    if start_mode == 3:
        return run_copy_cut_mode(base_answers)
    if start_mode == 2:
        try:
            load_answers_from_config(answers, config_path, skip_crop=True)
        except Exception as exc:
            fail(str(exc))
        answers["_question_offset"] = 1
        try:
            run_crop_only_prompt(answers)
            answers["_question_number"] = answers.get("_last_question_number", 1) + 1
            step_start_now(answers)
        except Back:
            note("Returning to main menu.")
            return None
    else:
        answers["_question_offset"] = 1
        try:
            run_wizard(answers)
        except Back:
            note("Returning to main menu.")
            return None

    cmd = answers["cmd"]
    if not answers.get("start_now", True):
        note("FFmpeg was not started. The command above is ready to run manually.")
        return None

    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    # Estimate total duration so the progress bar can compute percent / ETA.
    total_duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    log_info(f"Starting FFmpeg encode. Estimated source duration: "
             f"{format_elapsed(total_duration) if total_duration else 'unknown'}")
    if reverse_video_needs_segmented_main_encode(answers):
        return run_segmented_reverse_main_encode(answers)
    return_code, elapsed = run_ffmpeg_with_progress(
        cmd, total_duration=(total_duration if total_duration > 0 else None),
        label="FFmpeg encode",
    )
    return return_code, elapsed


def main() -> int:
    cli_args = sys.argv[1:]
    refresh_reference = False
    preview_colors = False
    leftover_args: list[str] = []
    for arg in cli_args:
        if arg in ("--refresh-ffmpeg-reference", "--regen-ffmpeg-reference"):
            refresh_reference = True
        elif arg in ("--preview-colors", "--preview-progress-colors"):
            preview_colors = True
        else:
            leftover_args.append(arg)
    if leftover_args:
        note(f"Ignoring unknown CLI arguments: {leftover_args}")
    if preview_colors:
        preview_console_colors()
        return 0

    title = "FFmpeg Wizard (FFmWiz)"
    terminal_width = shutil.get_terminal_size((80, 20)).columns
    title_padding = max(0, (terminal_width - len(title)) // 2)
    print((" " * title_padding) + paint(title, Color.BOLD + Color.WIZARD_TITLE))
    print(paint("=" * terminal_width, Color.WIZARD_TITLE))

    # Set up logging FIRST so every later action (auto-install, config
    # creation, FFmpeg runs, errors) is captured to a dated UTF-8 file.
    log_file = setup_logging()
    if log_file is not None:
        note(f"Logging to: {log_file}")
    log_environment({"CLI args": cli_args or "(none)"})

    ffmpeg, ffprobe = check_tools()
    log_info(f"FFmpeg: {ffmpeg}")
    log_info(f"FFprobe: {ffprobe}")
    config_path = default_config_path()
    launcher_path = default_launcher_path()
    reference_path = default_ffmpeg_reference_path()
    log_info(f"Config path: {config_path}")
    log_info(f"Launcher path: {launcher_path}")
    log_info(f"FFmpeg reference path: {reference_path}")
    try:
        ensure_config_file(config_path)
    except OSError as exc:
        note(f"Could not create config file next to the script: {exc}")
        log_warn(f"ensure_config_file failed: {exc}")
    try:
        ensure_launcher_file(launcher_path)
    except OSError as exc:
        note(f"Could not create launcher file next to the script: {exc}")
        log_warn(f"ensure_launcher_file failed: {exc}")
    try:
        ensure_ffmpeg_reference_file(reference_path, ffmpeg, force=refresh_reference)
    except Exception as exc:
        note(f"Could not refresh FFmpeg reference: {exc}")
        log_warn(f"ensure_ffmpeg_reference_file failed: {exc}")

    # Auto-install PySide6 (the runtime for the new Cut Editor / Crop
    # Preview GUIs) on first run so the new GUI is the default path. This
    # block runs only when PySide6 is missing; subsequent runs detect it
    # via the cached probe and skip the prompt entirely.
    try:
        ensure_pyside6_installed(interactive=True)
    except Exception as exc:
        note(f"PySide6 auto-install check failed: {exc}")

    base_answers: dict[str, Any] = {
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "muxers": list_muxers(ffmpeg),
        "video_encoders": list_encoders(ffmpeg, "video"),
        "audio_encoders": list_encoders(ffmpeg, "audio"),
        "detect_duplicate_audio": True,
    }

    first_run = True
    while True:
        if not first_run:
            print()
            note("Ready for a new job.")
            print()
        print_startup_banner(config_path, launcher_path)
        result = run_one_job(base_answers, config_path)
        if result is None:
            note("Returning to the first question.")
            first_run = False
            continue
        return_code, elapsed = result
        note(f"Total time elapsed: {format_elapsed(elapsed)}")
        log_info(f"Total time elapsed: {format_elapsed(elapsed)}")
        if return_code == 0:
            note("FFmpeg finished successfully. Returning to the first question.")
            log_info("FFmpeg finished successfully.")
        else:
            error(f"FFmpeg finished with exit code {return_code}. Returning to the first question.")
            log_error(f"FFmpeg finished with exit code {return_code}.")
        first_run = False


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExitWizard:
        log_info("Wizard exited via 'exit' command.")
        raise SystemExit(0)
    except KeyboardInterrupt:
        log_info("Wizard interrupted with Ctrl+C.")
        sys.stdout.write("\n")
        raise SystemExit(130)
    except SystemExit:
        raise
    except Exception:
        # Always preserve crash tracebacks in the log file even if the
        # error path above wasn't reached.
        log_exception("Unhandled exception in FFmWiz.main")
        error(f"Unhandled exception. See log file: {_log_file_text()}")
        if os.environ.get("FFMWIZ_DEBUG"):
            traceback.print_exc()
        raise SystemExit(1)







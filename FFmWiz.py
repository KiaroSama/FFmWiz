from __future__ import annotations

import datetime
import concurrent.futures
import csv
import html
import json
import logging
import math
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
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
LOUDNORM_DEFAULT_TARGET_I = -16.0
LOUDNORM_TARGET_TP = -1.5
LOUDNORM_TARGET_LRA = 11.0
LOUDNORM_MIN_TARGET_I = -30.0
LOUDNORM_MAX_TARGET_I = -5.0

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
COMMON_AUDIO_CODECS = ["aac", "libopus", "opus", "libmp3lame", "flac", "pcm_s16le", "copy"]
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
VOLUME_SCAN_WORKERS = max(1, env_int("FFMWIZ_VOLUME_SCAN_WORKERS", 3))
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

STREAM_STAT_METADATA_TAGS = (
    "BPS",
    "BPS-eng",
    "BPS-ENG",
    "DURATION",
    "DURATION-eng",
    "DURATION-ENG",
    "NUMBER_OF_FRAMES",
    "NUMBER_OF_FRAMES-eng",
    "NUMBER_OF_FRAMES-ENG",
    "NUMBER_OF_BYTES",
    "NUMBER_OF_BYTES-eng",
    "NUMBER_OF_BYTES-ENG",
    "_STATISTICS_WRITING_APP",
    "_STATISTICS_WRITING_DATE_UTC",
    "_STATISTICS_TAGS",
)

AUDIO_CODEC_ALIASES = {
    "opus": "libopus",
    "mp3": "libmp3lame",
    "mp3lame": "libmp3lame",
    "vorbis": "libvorbis",
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
        "modes": "Mode 1 = full interactive wizard (every question asked; can use the Premiere-style Unified Video Editor for crop, cuts, Split points, waveform preview, and speed/reverse in one workspace). Mode 2 = read this file, then only ask the crop question. Mode 3 = stream-copy cut tool (does not read this file). Mode 4 = folder encode. Mode 5 = add audio/subtitle files to a video without re-encoding and optionally set language/title metadata for added streams. Mode 6 = extract one video/audio/subtitle stream by ffprobe stream index. Mode 7 = write detailed ffprobe media info reports for a file or folder. Mode 8 = stream-cleanup remux for keeping selected audio/subtitle streams without re-encoding, optionally editing kept stream metadata, copying unchanged videos directly, and copying non-video files in folder mode. Mode 9 = hard-sub encode for burning an internal or external subtitle into the video. Mode 10 = video speed/reverse editor. Mode 11 = audio cut/speed/reverse editor. Mode 12 = join videos with stream copy when possible or re-encode when needed. Mode 13 = metadata editor for stream tags, dispositions, chapters, cover art, bitstream metadata, and metadata reports.",
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
        "audio_codec": "aac | libopus | opus | libmp3lame | flac | pcm_s16le | copy | <any encoder name from ffmpeg -encoders>. The opus alias is normalized to libopus to avoid FFmpeg's experimental native opus encoder. Container compatibility is enforced: WebM forces libopus; pcm_*/flac ignore bitrate; 'copy' skips re-encoding.",
        "audio_bitrate_kbps": "Target audio bitrate per stream in kbps. Used only for bitrate-based codecs (aac/libopus/libmp3lame/etc.). Use 'n' to keep the source bitrate. Interactive prompts warn before accepting a target above the detected selected source audio bitrate. Common values: 64, 96, 128, 160, 192, 256, 320.",
        "keep_source_metadata": "y/n. y keeps source container/stream metadata, chapters, extra source video/data streams, and allows subtitle stream selection. n removes metadata, chapters, extra source video streams, source subtitle/data streams, and embedded font/attachment streams from encode outputs.",
        "subtitle_tracks": "Selection for which subtitle streams to keep when keep_source_metadata is y. Same syntax as audio_tracks plus 'none' / 'clear' / 'delete' to drop all subtitles. MP4/MOV outputs convert text subtitles to mov_text and drop non-text (PGS, VobSub).",
        "keep_embedded_attachments": "y/n. y copies MKV attachment streams such as embedded subtitle fonts when keep_source_metadata is y and the output container supports attachments. Non-MKV outputs cannot keep attachment streams reliably here.",
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
        "keep_source_metadata": "y",
        "subtitle_tracks": "none",
        "keep_embedded_attachments": "n",
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
ATTACHMENT_COMPATIBLE_EXTS = {"mkv"}
ADD_FILES_OUTPUT_SUFFIX = "_with_tracks"
EXTRACT_STREAM_OUTPUT_SUFFIX = "_stream"
MEDIA_REPORTS_DIR_NAME = "MediaReports"
MUX_CLEANUP_VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".webm", ".mov", ".avi"}
ROBOCOPY_BIN = "robocopy"
HARDSUB_OUTPUT_SUFFIX = "_HardSub"
GENERATED_OUTPUT_SUFFIXES = (
    "_Encode",
    "_Final",
    HARDSUB_OUTPUT_SUFFIX,
    "_cut",
    ADD_FILES_OUTPUT_SUFFIX,
    EXTRACT_STREAM_OUTPUT_SUFFIX,
)
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

NVENC_MULTIPASS_MODES = {"disabled", "qres", "fullres"}

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
    # Distinct prompt option-key colors (high-contrast, not used elsewhere).
    OPT_KEY_CYAN = "\033[38;5;87m"     # Bright cyan-turquoise for bitrate/CRF keys.
    OPT_KEY_CHARTREUSE = "\033[38;5;154m"  # Bright yellow-green for y/n keys.
    OPT_KEY_CORAL = "\033[38;5;209m"   # Bright coral-orange for 0/1/2 keys.
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
    MAX_VOLUME = "\033[38;2;255;142;86m"
    MEAN_VOLUME = "\033[38;2;132;220;255m"
    CHAPTERS_YES = "\033[38;2;119;255;163m"
    CHAPTERS_NO = "\033[38;2;255;198;92m"
    UNIFIED_CAP_CROP = "\033[38;2;118;213;255m"
    UNIFIED_CAP_CUTS = "\033[38;2;255;122;122m"
    UNIFIED_CAP_SPEED = "\033[38;2;210;156;255m"
    UNIFIED_CAP_WAVEFORM = "\033[38;2;118;255;191m"
    WIZARD_TITLE = "\033[38;2;255;50;115m"
    MUX_GOLD = "\033[38;5;220m"
    MUX_AMBER = "\033[38;5;214m"
    MUX_MINT = "\033[38;5;121m"
    MUX_EMERALD = "\033[38;5;48m"
    MUX_TEAL = "\033[38;5;37m"
    MUX_AQUA = "\033[38;5;51m"
    MUX_SKY = "\033[38;5;117m"
    MUX_AZURE = "\033[38;5;75m"
    MUX_INDIGO = "\033[38;5;99m"
    MUX_VIOLET = "\033[38;5;135m"
    MUX_PURPLE = "\033[38;5;141m"
    MUX_LAVENDER = "\033[38;5;183m"
    MUX_ROSE = "\033[38;5;204m"
    MUX_CORAL = "\033[38;5;209m"
    MUX_SALMON = "\033[38;5;210m"
    MUX_STEEL = "\033[38;5;110m"
    MUX_SILVER = "\033[38;5;250m"
    MUX_HEADER = "\033[1m\033[38;2;255;50;115m"
    MUX_SCAN_HEADER = "\033[1m\033[38;2;68;221;255m"
    MUX_SUMMARY_HEADER = "\033[1m\033[38;2;170;255;82m"
    MUX_VERIFY_HEADER = "\033[1m\033[38;2;255;115;225m"
    MUX_CONFIRM_HEADER = "\033[1m\033[38;2;255;155;60m"
    MUX_PROCESS_HEADER = "\033[1m\033[38;2;80;255;205m"
    MUX_DONE_HEADER = "\033[1m\033[38;2;145;255;95m"
    MUX_SEPARATOR = "\033[1m\033[38;2;75;130;190m"
    MUX_FILE_LINE = "\033[1m\033[38;2;255;20;20m"
    MUX_SETTING_LABEL = "\033[1m\033[38;2;110;210;255m"
    MUX_SETTING_VALUE = "\033[38;2;245;245;245m"
    MUX_INPUT_PATH = "\033[38;2;70;255;210m"
    MUX_OUTPUT_BASE = "\033[38;2;255;105;180m"
    MUX_OUTPUT_ROOT = "\033[38;2;190;255;70m"
    MUX_MODE = "\033[1m\033[38;2;180;145;255m"
    MUX_AUDIO = "\033[38;2;120;255;170m"
    MUX_SUBTITLE = "\033[38;2;255;150;220m"
    MUX_TRUE = "\033[1m\033[38;2;95;255;120m"
    MUX_FALSE = "\033[1m\033[38;2;255;95;95m"
    MUX_UNKNOWN_LANGUAGE = "\033[38;5;244m"
    MUX_SIZE_DIFF = "\033[38;2;0;170;125m"
    MUX_ELAPSED = "\033[38;2;205;122;42m"


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


def step_is_auto_back_skip(step: Step, answers: dict[str, Any]) -> bool:
    """Return True for steps that may complete without showing a prompt."""
    if step.name == "audio_track" and len(answers.get("audio_streams") or []) <= 1:
        return True
    if step.name == "hardsub_audio_container":
        input_path = answers.get("input_path")
        input_ext = Path(input_path).suffix.lstrip(".").lower() if input_path else ""
        output_ext = str(answers.get("output_ext") or "").lstrip(".").lower()
        return answers.get("hardsub_audio_mode") == "none" or bool(input_ext and output_ext and input_ext == output_ext)
    return False


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


MUX_LANGUAGE_COLORS = (
    Color.GREEN,
    Color.CYAN,
    Color.MAGENTA,
    Color.YELLOW,
    Color.BLUE,
    Color.ORANGE,
    Color.MUX_GOLD,
    Color.LIME,
    Color.MUX_MINT,
    Color.MUX_EMERALD,
    Color.MUX_TEAL,
    Color.MUX_AQUA,
    Color.MUX_SKY,
    Color.MUX_AZURE,
    Color.MUX_INDIGO,
    Color.MUX_VIOLET,
    Color.MUX_PURPLE,
    Color.MUX_LAVENDER,
    Color.PINK,
    Color.MUX_ROSE,
)


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


def chapter_presence(payload_or_answers: dict[str, Any] | None) -> tuple[str, str]:
    data = payload_or_answers or {}
    chapters = data.get("chapters")
    if chapters is None and isinstance(data.get("probe"), dict):
        chapters = data["probe"].get("chapters")
    has_chapters = bool(chapters)
    return ("yes" if has_chapters else "no", Color.CHAPTERS_YES if has_chapters else Color.CHAPTERS_NO)


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


def normalize_separator_points(points: Any, duration: float) -> list[float]:
    duration = max(0.0, float(duration or 0.0))
    cleaned: list[float] = []
    if duration <= 0:
        return cleaned
    for value in points or []:
        try:
            point = float(value)
        except (TypeError, ValueError):
            continue
        if 1e-6 < point < duration - 1e-6:
            cleaned.append(point)
    return sorted(set(round(point, 6) for point in cleaned))


def separator_ranges(points: Any, duration: float) -> list[tuple[float, float]]:
    duration = max(0.0, float(duration or 0.0))
    if duration <= 0:
        return []
    normalized = normalize_separator_points(points, duration)
    boundaries = [0.0, *normalized, duration]
    ranges: list[tuple[float, float]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        if end > start + 1e-6:
            ranges.append((start, end))
    return ranges


def intersect_keep_ranges_with_segment(
    keep_ranges: list[tuple[float, float]],
    segment: tuple[float, float],
    duration: float,
) -> list[tuple[float, float]]:
    segment_start, segment_end = segment
    source_keeps = normalize_cut_ranges(keep_ranges, duration)
    if not source_keeps:
        source_keeps = [(segment_start, segment_end)]
    intersections: list[tuple[float, float]] = []
    for start, end in source_keeps:
        clipped_start = max(float(start), segment_start)
        clipped_end = min(float(end), segment_end)
        if clipped_end > clipped_start + 1e-6:
            intersections.append((clipped_start, clipped_end))
    return normalize_cut_ranges(intersections, duration)


def final_processed_duration_for_splits(answers: dict[str, Any], source_duration: float) -> float:
    duration = max(0.0, float(source_duration or 0.0))
    keep_ranges = normalize_cut_ranges(list(answers.get("cut_keep_ranges") or []), duration)
    if keep_ranges:
        duration = total_keep_duration(keep_ranges)
    if video_speed_transform_enabled(answers):
        duration = duration / max(0.001, encode_video_speed_factor(answers))
    return max(0.0, duration)


def ffmpeg_progress_duration_for_answers(answers: dict[str, Any], source_duration: float) -> float:
    final_duration = final_processed_duration_for_splits(answers, source_duration)
    split_points = normalize_separator_points(answers.get("separator_points"), final_duration)
    if split_points:
        # FFmpeg's -progress out_time is per active output in multi-output Split
        # commands. FFmWiz reconstructs aggregate Split progress from part
        # durations plus the active output timestamp, so the progress duration
        # must be the full processed program duration rather than only the last
        # part.
        return final_duration
    return final_duration


def split_part_output_paths(
    output_path: Path,
    part_count: int,
    input_paths: list[Path] | None = None,
) -> list[Path]:
    input_paths = input_paths or []
    stem = sanitize_output_stem(output_path.stem)
    suffix = output_path.suffix or ".mp4"
    paths: list[Path] = []
    for index in range(1, int(part_count) + 1):
        candidate = output_path.with_name(f"{stem}_Part{index:02d}{suffix}")
        candidate = resolve_output_collision_against_inputs(candidate, input_paths, "_Final")
        candidate = unique_numbered_path(candidate)
        paths.append(candidate)
    return paths


def append_final_split_filters(
    filters: list[str],
    video_label: str,
    audio_labels: list[str],
    split_points: Any,
    final_duration: float,
    prefix: str,
    fps: float = 0.0,
) -> tuple[list[str], list[list[str]], list[tuple[float, float]]]:
    intervals = separator_ranges(split_points, final_duration)
    if len(intervals) <= 1:
        return [video_label], [[label for label in audio_labels]], intervals
    # Snap each interior split boundary to the OUTPUT frame grid so every part
    # starts/ends exactly on a frame after any fps change (no fractional first
    # frame). The clip's own start (0) and end (final_duration) are left as-is.
    if fps and float(fps) > 0:
        frame = 1.0 / float(fps)
        bounds = (
            [intervals[0][0]]
            + [round(round(iv[1] / frame) * frame, 6) for iv in intervals[:-1]]
            + [intervals[-1][1]]
        )
        snapped = [(s, e) for s, e in zip(bounds, bounds[1:]) if e > s + 1e-6]
        if snapped and snapped != intervals:
            log_info(f"Split boundaries snapped to {float(fps):g} fps frame grid: {intervals} -> {snapped}")
            intervals = snapped
    part_count = len(intervals)
    video_sources = [f"{prefix}vpart{idx}_src" for idx in range(part_count)]
    filters.append(f"[{video_label}]split={part_count}{''.join(f'[{label}]' for label in video_sources)}")
    log_info(f"Filter graph decision: final Split enabled; video split={part_count}; intervals={intervals}")
    video_outputs: list[str] = []
    for idx, (start, end) in enumerate(intervals):
        out_label = f"{prefix}vout{idx}"
        video_outputs.append(out_label)
        filters.append(
            f"[{video_sources[idx]}]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[{out_label}]"
        )
    audio_outputs_by_part: list[list[str]] = [[] for _ in intervals]
    for audio_pos, audio_label in enumerate(audio_labels):
        audio_sources = [f"{prefix}apart{idx}_{audio_pos}_src" for idx in range(part_count)]
        filters.append(f"[{audio_label}]asplit={part_count}{''.join(f'[{label}]' for label in audio_sources)}")
        log_info(f"Filter graph decision: final Split enabled; audio label [{audio_label}] asplit={part_count}.")
        for idx, (start, end) in enumerate(intervals):
            out_label = f"{prefix}aout{idx}_{audio_pos}"
            audio_outputs_by_part[idx].append(out_label)
            filters.append(
                f"[{audio_sources[idx]}]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[{out_label}]"
            )
    return video_outputs, audio_outputs_by_part, intervals


def append_video_encode_options(
    cmd: list[str],
    answers: dict[str, Any],
    video_encoder: str,
    tag: str | None,
    profile: str | None,
) -> None:
    cmd.extend(["-c:v", video_encoder])
    if str(video_encoder).endswith("_nvenc"):
        cmd.extend(["-preset", NVENC_PRESET, "-tune", NVENC_TUNE, "-rc", NVENC_RC])
        append_nvenc_multipass_args(cmd, answers, video_encoder)
        if "hevc" in str(video_encoder):
            cmd.extend(["-profile:v", hevc_profile_for_output(answers, profile)])
    elif video_encoder in {"libx264", "libx265"}:
        cmd.extend(["-preset", CPU_PRESET])
        if video_encoder == "libx265":
            cmd.extend(["-profile:v", hevc_profile_for_output(answers, "main")])
    video_bitrate = answers.get("video_bitrate_kbps")
    if video_bitrate:
        append_video_bitrate_args(cmd, answers, int(video_bitrate))
    elif answers.get("video_crf") is not None:
        crf_value = answers["video_crf"]
        if str(video_encoder).endswith("_nvenc"):
            cmd[cmd.index("-rc") + 1] = "constqp"
            cmd.extend(["-cq:v", str(int(round(crf_value))), "-b:v", "0"])
        else:
            cmd.extend(["-crf", f"{crf_value:g}"])
    cmd.extend(["-color_range:v:0", COLOR_RANGE])
    if tag and str(answers.get("output_ext", "")).lower() in MP4_LIKE_EXTS:
        cmd.extend(["-tag:v", tag])


def is_nvenc_multipass_encoder(video_encoder: Any) -> bool:
    return str(video_encoder or "").strip().lower() in {"hevc_nvenc", "h264_nvenc"}


def normalize_nvenc_multipass_mode(value: Any) -> str:
    mode = str(value or "disabled").strip().lower()
    return mode if mode in NVENC_MULTIPASS_MODES else "disabled"


def nvenc_multipass_default_mode(answers: dict[str, Any], quality_oriented: bool = True) -> str:
    if not quality_oriented:
        return "disabled"
    # Default to qres (quarter-resolution first pass): a good quality/speed balance
    # and noticeably faster than fullres, which most users do not need by default.
    return "qres"


def nvenc_multipass_args(mode: Any) -> list[str]:
    normalized = normalize_nvenc_multipass_mode(mode)
    if normalized in {"qres", "fullres"}:
        return ["-multipass", normalized]
    return []


def append_nvenc_multipass_args(cmd: list[str], answers: dict[str, Any], video_encoder: Any) -> None:
    if not is_nvenc_multipass_encoder(video_encoder):
        return
    mode = normalize_nvenc_multipass_mode(answers.get("nvenc_multipass"))
    args = nvenc_multipass_args(mode)
    if args:
        log_info(f"NVENC multipass option inserted: encoder={video_encoder}; mode={mode}; args={args}")
    else:
        log_info(f"NVENC multipass disabled for encoder={video_encoder}; no -multipass option inserted.")
    cmd.extend(args)


def set_nvenc_multipass_skip_reason(answers: dict[str, Any], reason: str) -> None:
    answers["nvenc_multipass_skip_reason"] = reason
    log_info(f"NVENC multipass skipped: {reason}")


def nvenc_multipass_applicable_for_encoder(
    answers: dict[str, Any],
    video_encoder: Any,
    *,
    video_reencode: bool = True,
    pure_copy: bool = False,
    workflow_name: str = "video encode",
) -> bool:
    if not video_reencode:
        set_nvenc_multipass_skip_reason(answers, "no video re-encode")
        return False
    if pure_copy:
        set_nvenc_multipass_skip_reason(answers, "pure copy/remux workflow")
        return False
    encoder = str(video_encoder or "").strip().lower()
    if encoder in {"", "copy"}:
        set_nvenc_multipass_skip_reason(answers, "video codec is copy")
        return False
    if not is_nvenc_multipass_encoder(encoder):
        reason = "CPU encoder selected" if not encoder.endswith("_nvenc") else f"unsupported NVENC encoder {encoder}"
        set_nvenc_multipass_skip_reason(answers, reason)
        return False
    answers.pop("nvenc_multipass_skip_reason", None)
    log_info(f"NVENC multipass applicable for {workflow_name}: encoder={encoder}")
    return True


def resolved_video_encoder_for_nvenc_multipass(answers: dict[str, Any]) -> str:
    if not output_has_video(answers):
        return ""
    video_encoder, tag, profile = resolve_video_encoder(answers)
    if video_encoder == "copy" and video_filters_required(answers):
        fallback_answers = dict(answers)
        fallback_answers["video_codec"] = DEFAULT_VIDEO_CODEC
        video_encoder, tag, profile = resolve_video_encoder(fallback_answers)
    video_encoder, _tag, _profile = enforce_bit_depth_compatible_video_encoder(
        answers,
        video_encoder,
        tag,
        profile,
    )
    return video_encoder


def nvenc_multipass_prompt_applicable(answers: dict[str, Any]) -> bool:
    # Applicability must depend only on the workflow shape (NVENC video
    # re-encode), not on whether the value was already answered. Otherwise the
    # step would vanish during back navigation and shift later question
    # numbers once the user answered it.
    if not output_has_video(answers):
        set_nvenc_multipass_skip_reason(answers, "audio-only workflow")
        return False
    if str(answers.get("video_codec") or "").strip().lower() in {"copy", "n"}:
        set_nvenc_multipass_skip_reason(answers, "video codec is copy")
        return False
    video_encoder = resolved_video_encoder_for_nvenc_multipass(answers)
    return nvenc_multipass_applicable_for_encoder(answers, video_encoder, workflow_name="main encode")


def ask_nvenc_multipass_if_applicable(
    answers: dict[str, Any],
    *,
    video_encoder: Any | None = None,
    workflow_name: str = "video encode",
    video_reencode: bool = True,
    quality_oriented: bool = True,
    pure_copy: bool = False,
) -> str:
    encoder = video_encoder if video_encoder is not None else resolved_video_encoder_for_nvenc_multipass(answers)
    if not nvenc_multipass_applicable_for_encoder(
        answers,
        encoder,
        video_reencode=video_reencode,
        pure_copy=pure_copy,
        workflow_name=workflow_name,
    ):
        return "disabled"
    # Re-prompt on every entry (including back navigation). When a value was
    # already chosen, offer it as the default so pressing Enter keeps it.
    previous_mode = answers.get("nvenc_multipass")
    if previous_mode in NVENC_MULTIPASS_MODES:
        default_mode = normalize_nvenc_multipass_mode(previous_mode)
    else:
        default_mode = nvenc_multipass_default_mode(answers, quality_oriented)
    default_choice = {"disabled": "0", "qres": "1", "fullres": "2"}[default_mode]
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Use NVENC multipass?",
                f"{paint('0', Color.OPT_KEY_CORAL)}{paint('=Disabled / fastest', Color.HINT_YELLOW)}; "
                f"{paint('1', Color.OPT_KEY_CORAL)}{paint('=qres / quarter-resolution first pass', Color.HINT_YELLOW)}; "
                f"{paint('2', Color.OPT_KEY_CORAL)}{paint('=fullres / best quality, slower', Color.HINT_YELLOW)}",
                default_choice,
                back="back=b, quit=exit",
            )
        )
        lowered = value.strip().lower()
        if lowered in {"b", "back"}:
            raise Back()
        if not lowered:
            lowered = default_choice
            default_used = True
        else:
            default_used = False
        mapping = {"0": "disabled", "۱": "qres", "١": "qres", "1": "qres", "۲": "fullres", "٢": "fullres", "2": "fullres"}
        if lowered in {"۰", "٠"}:
            lowered = "0"
        mode = mapping.get(lowered)
        if mode:
            answers["nvenc_multipass"] = mode
            answers.pop("nvenc_multipass_skip_reason", None)
            log_info(
                f"User choice: nvenc_multipass={mode}; workflow={workflow_name}; "
                f"default_used={'yes' if default_used else 'no'}"
            )
            return mode
        error("Enter 0, 1, or 2. Use b to go back.")


def step_nvenc_multipass(answers: dict[str, Any]) -> None:
    ask_nvenc_multipass_if_applicable(answers, workflow_name="main encode", quality_oriented=True)


def append_audio_encode_options(cmd: list[str], answers: dict[str, Any], has_audio: bool) -> None:
    if not has_audio:
        cmd.append("-an")
        return
    audio_codec = normalize_audio_codec(
        answers.get("audio_codec"),
        default_audio_codec_for_ext(answers.get("output_ext", "")),
    )
    answers["audio_codec"] = audio_codec
    if audio_codec == "copy":
        note("Audio copy cannot be used after Split/filter processing. AAC was selected for audio.")
        audio_codec = DEFAULT_AUDIO_CODEC
        answers["audio_codec"] = audio_codec
    cmd.extend(["-c:a", audio_codec])
    audio_bitrate = answers.get("audio_bitrate_kbps")
    if audio_bitrate and audio_codec_uses_bitrate(str(audio_codec)):
        cmd.extend(["-b:a", f"{audio_bitrate}k"])
    if AUDIO_CHANNELS:
        cmd.extend(["-ac", str(AUDIO_CHANNELS)])
    if AUDIO_SAMPLE_RATE:
        cmd.extend(["-ar", str(AUDIO_SAMPLE_RATE)])


def append_container_options(cmd: list[str], output_ext: str) -> None:
    if output_ext.lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])


def timeline_is_modified(answers: dict[str, Any]) -> bool:
    """Return True when the output timeline differs from the source timeline.

    Timeline-modifying operations include multi-range cuts, speed changes,
    video reversal, split into multiple parts, and join of multiple inputs.
    A single-range cut (trim) also modifies the timeline because chapter
    timestamps must be offset to begin at zero.
    """
    if answers.get("cut_keep_ranges"):
        return True
    if video_speed_transform_enabled(answers):
        return True
    if answers.get("reverse_video"):
        return True
    if answers.get("separator_points"):
        return True
    if answers.get("join_input_items"):
        return True
    return False


def remap_chapters_for_encode(
    answers: dict[str, Any],
    speed_factor: float = 1.0,
    part_interval: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Remap source chapters to the processed output timeline.

    Returns a plan dict compatible with `copy_cut_chapter_map_args`:
      mode="copy"     – use -map_chapters 0 (no timeline change, chapters valid)
      mode="drop"     – use -map_chapters -1 (no chapters survive)
      mode="metadata" – use a generated FFmetadata file
      mode="disable"  – use -map_chapters -1 (remapping unavailable)

    Parameters
    ----------
    answers : dict
        The standard wizard answers dict containing probe data.
    speed_factor : float
        The video speed multiplier (>1 = faster, <1 = slower).
    part_interval : tuple[float, float] | None
        If the output is split into parts, the (start, end) interval of this
        part in the *processed* timeline (after cuts + speed). Chapters are
        clipped to this interval and timestamps offset to start at zero.
    """
    probe = answers.get("probe") if isinstance(answers.get("probe"), dict) else {}
    chapters = (probe or {}).get("chapters") or []
    chapters = [ch for ch in chapters if isinstance(ch, dict)]
    if not chapters:
        log_info("Chapters: source contains no chapters")
        return {"mode": "copy", "chapters": [], "overlap_count": 0}

    if not timeline_is_modified(answers):
        log_info("Chapters: preserved from source")
        return {"mode": "copy", "chapters": [], "overlap_count": 0}

    # Determine keep ranges; default to full source duration.
    source_duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    for ch in chapters:
        end = copy_cut_chapter_seconds(ch, "end")
        if end is not None:
            source_duration = max(source_duration, end)
    source_duration = max(0.0, source_duration)

    keep_ranges = normalize_cut_ranges(list(answers.get("cut_keep_ranges") or []), source_duration)
    if not keep_ranges:
        keep_ranges = [(0.0, source_duration)]

    # Remap each chapter through retained ranges.
    remapped: list[dict[str, Any]] = []
    for ch in chapters:
        start = copy_cut_chapter_seconds(ch, "start")
        end = copy_cut_chapter_seconds(ch, "end")
        if start is None or end is None or end <= start:
            continue

        # Compute output position by accumulating kept ranges.
        output_offset = 0.0
        ch_new_start: float | None = None
        ch_new_end: float | None = None

        for keep_start, keep_end in keep_ranges:
            keep_duration = keep_end - keep_start
            # Chapter must overlap this kept range to survive.
            overlap_start = max(start, keep_start)
            overlap_end = min(end, keep_end)
            if overlap_end > overlap_start + 1e-6:
                seg_start = output_offset + (overlap_start - keep_start)
                seg_end = output_offset + (overlap_end - keep_start)
                if ch_new_start is None:
                    ch_new_start = seg_start
                ch_new_end = seg_end
            output_offset += keep_duration

        if ch_new_start is None or ch_new_end is None:
            continue

        # Apply speed factor.
        if speed_factor > 0 and abs(speed_factor - 1.0) > 1e-9:
            ch_new_start = ch_new_start / speed_factor
            ch_new_end = ch_new_end / speed_factor

        if ch_new_end <= ch_new_start + 1e-6:
            continue

        remapped.append({
            "start": ch_new_start,
            "end": ch_new_end,
            "metadata": dict(ch.get("tags") or {}),
        })

    # Clip to part interval if splitting.
    if part_interval is not None:
        part_start, part_end = part_interval
        part_chapters: list[dict[str, Any]] = []
        for ch in remapped:
            clip_start = max(ch["start"], part_start)
            clip_end = min(ch["end"], part_end)
            if clip_end > clip_start + 1e-6:
                part_chapters.append({
                    "start": clip_start - part_start,
                    "end": clip_end - part_start,
                    "metadata": dict(ch.get("metadata") or {}),
                })
        remapped = part_chapters

    if not remapped:
        log_info("Chapters: disabled because timeline changed and all chapters were removed")
        return {"mode": "drop", "chapters": [], "overlap_count": 0}

    log_info(f"Chapters: remapped to processed timeline ({len(remapped)} chapter(s) retained)")
    return {"mode": "metadata", "chapters": remapped, "overlap_count": 0}


def write_encode_chapter_metadata(plan: dict[str, Any], temp_dir: Path, suffix: str = "") -> Path:
    """Write an FFmetadata file for remapped encode chapters."""
    metadata_path = temp_dir / f"chapters_encode{suffix}.ffmetadata"
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


def append_source_metadata_chapter_options(cmd: list[str], answers: dict[str, Any]) -> None:
    if not output_has_video(answers):
        return
    cmd.extend(["-map_metadata", "0" if source_metadata_keep_enabled(answers) else "-1"])

    # Chapter handling: if user disabled chapters, always drop.
    if not source_chapters_keep_enabled(answers):
        cmd.extend(["-map_chapters", "-1"])
        return

    # If the timeline is not modified, preserve source chapters directly.
    if not timeline_is_modified(answers):
        cmd.extend(["-map_chapters", "0"])
        return

    # If a chapter metadata input was injected, use its index.
    chapter_input_index = answers.get("_chapter_metadata_input_index")
    if chapter_input_index is not None:
        cmd.extend(["-map_chapters", str(chapter_input_index)])
        log_info(f"Chapters: remapped to processed timeline (metadata input {chapter_input_index})")
        return

    # Timeline is modified but no metadata was prepared – disable chapters.
    cmd.extend(["-map_chapters", "-1"])
    log_info("Chapters: disabled because timeline changed and remapping was unavailable")


def append_clear_stream_stat_metadata(cmd: list[str], stream_spec: str) -> None:
    for key in STREAM_STAT_METADATA_TAGS:
        cmd.extend([f"-metadata:s:{stream_spec}", f"{key}="])


def append_clear_reencoded_stream_stat_metadata(
    cmd: list[str],
    answers: dict[str, Any],
    *,
    video_output_count: int = 0,
    audio_output_count: int = 0,
    subtitle_output_count: int = 0,
) -> None:
    if not source_metadata_keep_enabled(answers):
        return
    cleared: list[str] = []
    if int(video_output_count or 0) > 0:
        append_clear_stream_stat_metadata(cmd, "v")
        cleared.append("v:*")
    if int(audio_output_count or 0) > 0:
        append_clear_stream_stat_metadata(cmd, "a")
        cleared.append("a:*")
    if int(subtitle_output_count or 0) > 0:
        append_clear_stream_stat_metadata(cmd, "s")
        cleared.append("s:*")
    if cleared:
        log_info(
            "Cleared copied stream statistics metadata for processed output streams: "
            + ", ".join(cleared)
        )


def source_metadata_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("keep_source_metadata", True))


def source_chapters_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("keep_source_chapters", True))


def source_subtitles_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("keep_source_subtitles", True))


def source_data_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("keep_source_data_streams", source_metadata_keep_enabled(answers)))


def source_extra_video_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("keep_source_extra_video_streams", source_metadata_keep_enabled(answers)))


def source_chapter_streams(answers: dict[str, Any]) -> list[dict[str, Any]]:
    probe = answers.get("probe") if isinstance(answers.get("probe"), dict) else {}
    chapters = probe.get("chapters") if isinstance(probe, dict) else []
    return [chapter for chapter in (chapters or []) if isinstance(chapter, dict)]


def source_metadata_tags_present(answers: dict[str, Any]) -> bool:
    fmt = answers.get("format") if isinstance(answers.get("format"), dict) else {}
    if isinstance(fmt, dict) and fmt.get("tags"):
        return True
    streams: list[dict[str, Any]] = []
    for key in ("video_streams", "audio_streams", "subtitle_streams", "attachment_streams", "data_streams"):
        streams.extend(stream for stream in (answers.get(key) or []) if isinstance(stream, dict))
    return any(bool(stream.get("tags")) for stream in streams)


def streams_for_statistics_from_answers(answers: dict[str, Any]) -> list[dict[str, Any]]:
    probe = answers.get("probe") if isinstance(answers.get("probe"), dict) else {}
    probe_streams = probe.get("streams") if isinstance(probe, dict) else None
    if isinstance(probe_streams, list) and probe_streams:
        return [stream for stream in probe_streams if isinstance(stream, dict)]
    streams: list[dict[str, Any]] = []
    for key in ("video_streams", "audio_streams", "subtitle_streams", "attachment_streams", "data_streams"):
        streams.extend(stream for stream in (answers.get(key) or []) if isinstance(stream, dict))
    return streams


def source_extra_preservation_features(answers: dict[str, Any]) -> list[str]:
    features: list[str] = []
    if source_metadata_tags_present(answers):
        features.append("container/stream metadata")
    if source_chapter_streams(answers):
        features.append("chapters")
    if answers.get("subtitle_streams"):
        features.append("subtitle streams")
    if embedded_attachment_streams(answers):
        features.append("embedded font/attachment streams")
    if source_data_streams(answers):
        features.append("data streams")
    if additional_source_video_streams(answers):
        features.append("additional video streams")
    return features


def source_extra_policy_applicable(answers: dict[str, Any]) -> bool:
    return output_has_video(answers) and bool(source_extra_preservation_features(answers))


def source_video_stream(answers: dict[str, Any]) -> dict[str, Any] | None:
    streams = answers.get("video_streams") or []
    return streams[0] if streams else None


def additional_source_video_streams(answers: dict[str, Any]) -> list[dict[str, Any]]:
    streams = answers.get("video_streams") or []
    return list(streams[1:]) if len(streams) > 1 else []


def source_video_bit_depth(answers: dict[str, Any]) -> int | None:
    stream = source_video_stream(answers)
    return video_bit_depth(stream) if stream else None


def output_video_bit_depth(answers: dict[str, Any]) -> int:
    depth = source_video_bit_depth(answers)
    if not depth or depth <= 8:
        return 8
    return min(depth, 16)


def cpu_pixel_format_for_output(answers: dict[str, Any]) -> str:
    depth = output_video_bit_depth(answers)
    if depth <= 8:
        return CPU_FORMAT
    if depth <= 10:
        return "yuv420p10le"
    if depth <= 12:
        return "yuv420p12le"
    if depth <= 14:
        return "yuv420p14le"
    return "yuv420p16le"


def cuda_pixel_format_for_output(answers: dict[str, Any]) -> str:
    return "p010le" if output_video_bit_depth(answers) > 8 else CUDA_FORMAT


def hevc_profile_for_output(answers: dict[str, Any], default_profile: str | None = None) -> str:
    depth = output_video_bit_depth(answers)
    if depth <= 8:
        return default_profile or NVENC_HEVC_PROFILE
    if depth <= 10:
        return "main10"
    if depth <= 12:
        return "main12"
    return "rext"


def high_bit_depth_requires_cpu_encoder(answers: dict[str, Any], video_encoder: str) -> bool:
    return output_video_bit_depth(answers) > 10 and str(video_encoder).endswith("_nvenc")


def cpu_encoder_for_high_bit_depth(answers: dict[str, Any], video_encoder: str) -> tuple[str, str | None, str | None]:
    requested = str(answers.get("video_codec") or DEFAULT_VIDEO_CODEC).lower()
    info = VIDEO_CODEC_ALIASES.get(requested)
    if info and info.get("cpu"):
        cpu_encoder = str(info["cpu"])
        tag = info.get("tag")
        profile = info.get("profile")
    elif "hevc" in str(video_encoder) or "h265" in requested:
        cpu_encoder, tag, profile = "libx265", "hvc1", NVENC_HEVC_PROFILE
    elif "av1" in str(video_encoder) or requested == "av1":
        cpu_encoder, tag, profile = "libaom-av1", None, None
    else:
        cpu_encoder, tag, profile = "libx265", "hvc1", NVENC_HEVC_PROFILE
    if cpu_encoder == "libx264" and output_video_bit_depth(answers) > 10:
        note("H.264/NVENC cannot safely preserve source bit depth above 10-bit here. H.265 CPU encoding was selected to preserve high bit depth.")
        cpu_encoder, tag, profile = "libx265", "hvc1", NVENC_HEVC_PROFILE
        answers["video_codec"] = "H265"
    return cpu_encoder, tag, profile


def enforce_bit_depth_compatible_video_encoder(
    answers: dict[str, Any],
    video_encoder: str,
    tag: str | None,
    profile: str | None,
) -> tuple[str, str | None, str | None]:
    if high_bit_depth_requires_cpu_encoder(answers, video_encoder):
        source_depth = source_video_bit_depth(answers)
        target_depth = output_video_bit_depth(answers)
        cpu_encoder, cpu_tag, cpu_profile = cpu_encoder_for_high_bit_depth(answers, video_encoder)
        note(
            f"Source video is {source_depth}-bit. NVENC output is limited to 10-bit here, "
            f"so {cpu_encoder} was selected to preserve {target_depth}-bit output."
        )
        return cpu_encoder, cpu_tag if cpu_tag is not None else tag, cpu_profile if cpu_profile is not None else profile
    return video_encoder, tag, profile


def output_supports_embedded_attachments(answers: dict[str, Any]) -> bool:
    return str(answers.get("output_ext") or "").lower().lstrip(".") in ATTACHMENT_COMPATIBLE_EXTS


def embedded_attachment_streams(answers: dict[str, Any]) -> list[dict[str, Any]]:
    return list(answers.get("attachment_streams") or [])


def source_data_streams(answers: dict[str, Any]) -> list[dict[str, Any]]:
    return list(answers.get("data_streams") or [])


def embedded_attachment_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(
        answers.get("keep_embedded_attachments")
        and embedded_attachment_streams(answers)
        and output_supports_embedded_attachments(answers)
    )


def append_embedded_attachment_maps(cmd: list[str], answers: dict[str, Any]) -> bool:
    if not answers.get("keep_embedded_attachments"):
        return False
    streams = embedded_attachment_streams(answers)
    if not streams:
        return False
    if not output_supports_embedded_attachments(answers):
        log_info(
            "Embedded attachments were requested but not mapped because the output "
            f"container does not support MKV attachment streams reliably: {answers.get('output_ext')}"
        )
        return False
    cmd.extend(["-map", "0:t?"])
    log_info(f"Embedded attachment streams mapped for copy: count={len(streams)}")
    return True


def append_source_data_maps(cmd: list[str], answers: dict[str, Any]) -> bool:
    streams = source_data_streams(answers)
    if not streams:
        return False
    if not source_data_keep_enabled(answers):
        return False
    for index, _stream in enumerate(streams):
        cmd.extend(["-map", f"0:d:{index}"])
    log_info(f"Source data streams mapped for copy: count={len(streams)}")
    return True


def can_map_additional_source_video_streams(answers: dict[str, Any]) -> bool:
    if not additional_source_video_streams(answers) or not source_extra_video_keep_enabled(answers):
        return False
    if answers.get("separator_points") or video_speed_transform_enabled(answers):
        log_info("Additional source video streams were not mapped because final timeline Split/speed processing is active.")
        return False
    if answers.get("cut_keep_ranges"):
        log_info("Additional source video streams were not mapped because frame-accurate cuts are active.")
        return False
    return True


def append_additional_source_video_maps(cmd: list[str], answers: dict[str, Any]) -> int:
    if not can_map_additional_source_video_streams(answers):
        return 0
    count = 0
    for relative_index, _stream in enumerate(additional_source_video_streams(answers), start=1):
        cmd.extend(["-map", f"0:v:{relative_index}"])
        count += 1
    if count:
        log_info(f"Additional source video streams mapped for copy: count={count}")
    return count


def append_additional_source_video_codec_options(cmd: list[str], count: int) -> None:
    for relative_index in range(1, count + 1):
        cmd.extend([f"-c:v:{relative_index}", "copy"])


def _all_stream_indexes_selected(selected: Any, count: int) -> bool:
    if count <= 0:
        return True
    if selected == "all":
        return True
    if isinstance(selected, list):
        try:
            return sorted(int(item) for item in selected) == list(range(count))
        except (TypeError, ValueError):
            return False
    return False


def _explicit_all_streams_selected(selected: Any, count: int) -> bool:
    if count <= 0:
        return True
    return selected == "all"


def can_use_full_source_map_for_simple_encode(
    answers: dict[str, Any],
    audio_indices: list[int],
    audio_transform_active: bool,
    multi_cut: bool,
) -> bool:
    if not output_has_video(answers):
        return False
    if answers.get("join_input_items"):
        return False
    if multi_cut or audio_transform_active:
        return False
    if answers.get("separator_points") or video_speed_transform_enabled(answers):
        return False
    if not (
        source_metadata_keep_enabled(answers)
        and source_chapters_keep_enabled(answers)
        and source_extra_video_keep_enabled(answers)
        and source_subtitles_keep_enabled(answers)
        and source_data_keep_enabled(answers)
    ):
        return False
    if not _explicit_all_streams_selected(answers.get("audio_tracks"), len(answers.get("audio_streams") or [])):
        return False
    if not _explicit_all_streams_selected(answers.get("subtitle_tracks"), len(answers.get("subtitle_streams") or [])):
        return False
    if embedded_attachment_streams(answers) and not embedded_attachment_keep_enabled(answers):
        return False
    if answers.get("output_ext", "").lower() in MP4_LIKE_EXTS and answers.get("subtitle_streams"):
        return False
    return True


def append_source_data_codec_options(cmd: list[str], answers: dict[str, Any]) -> None:
    if source_data_streams(answers) and source_data_keep_enabled(answers):
        cmd.extend(["-c:d", "copy"])


def append_negative_stream_options(
    cmd: list[str],
    answers: dict[str, Any],
    has_video: bool,
    subtitle_indices: list[int],
    data_mapped: bool,
) -> None:
    if not has_video:
        cmd.append("-vn")
    if answers.get("subtitle_streams") and not subtitle_indices:
        cmd.append("-sn")
    if source_data_streams(answers) and not data_mapped:
        cmd.append("-dn")


def append_embedded_attachment_codec_options(cmd: list[str], answers: dict[str, Any]) -> None:
    if embedded_attachment_keep_enabled(answers):
        cmd.extend(["-c:t", "copy"])


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
    if abs(speed - 1.0) > 1e-6:
        filters.append(atempo_filter_chain(speed))
    return ",".join(filters)


def loudnorm_transform_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("loudnorm_enabled"))


def loudnorm_number(value: Any) -> str:
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def parse_loudnorm_target(value: str) -> float:
    try:
        target = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("Enter a numeric LUFS value.")
    if target < LOUDNORM_MIN_TARGET_I or target > LOUDNORM_MAX_TARGET_I:
        raise ValueError(
            f"Target I must be between {LOUDNORM_MIN_TARGET_I:g} and {LOUDNORM_MAX_TARGET_I:g} LUFS."
        )
    return target


def build_loudnorm_filter(answers: dict[str, Any]) -> str:
    target_i = float(answers.get("loudnorm_target_i", LOUDNORM_DEFAULT_TARGET_I))
    measured = answers.get("loudnorm_measured") if isinstance(answers.get("loudnorm_measured"), dict) else {}
    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if measured and all(measured.get(key) not in {None, ""} for key in required):
        # Determine whether linear normalization is feasible.
        measured_i = float(measured["input_i"])
        measured_tp = float(measured["input_tp"])
        required_gain = target_i - measured_i
        predicted_tp = measured_tp + required_gain
        target_tp = float(LOUDNORM_TARGET_TP)
        use_linear = predicted_tp <= target_tp

        if use_linear:
            linear_text = "true"
            log_info(
                "LoudNorm mode: Linear (measured two-pass); "
                f"target_i={target_i:g}; measured_I={measured_i:g}; measured_TP={measured_tp:g}; "
                f"gain={required_gain:+.2f} dB; predicted_TP={predicted_tp:.2f} dBTP; "
                f"target_TP={target_tp:g} dBTP; linear=true"
            )
        else:
            linear_text = "false"
            log_info(
                "LoudNorm mode: Dynamic; "
                f"Reason: Linear gain would raise predicted true peak to {predicted_tp:+.2f} dBTP, "
                f"above the selected {target_tp:g} dBTP limit. "
                f"target_i={target_i:g}; measured_I={measured_i:g}; measured_TP={measured_tp:g}; "
                f"gain={required_gain:+.2f} dB; linear=false"
            )

        return (
            "loudnorm="
            f"I={loudnorm_number(target_i)}:"
            f"TP={loudnorm_number(LOUDNORM_TARGET_TP)}:"
            f"LRA={loudnorm_number(LOUDNORM_TARGET_LRA)}:"
            f"measured_I={loudnorm_number(measured['input_i'])}:"
            f"measured_TP={loudnorm_number(measured['input_tp'])}:"
            f"measured_LRA={loudnorm_number(measured['input_lra'])}:"
            f"measured_thresh={loudnorm_number(measured['input_thresh'])}:"
            f"offset={loudnorm_number(measured['target_offset'])}:"
            f"linear={linear_text}:print_format=summary"
        )
    log_info(f"Using single-pass loudnorm filter because measured values are unavailable: target_i={target_i:g}")
    return (
        "loudnorm="
        f"I={loudnorm_number(target_i)}:"
        f"TP={loudnorm_number(LOUDNORM_TARGET_TP)}:"
        f"LRA={loudnorm_number(LOUDNORM_TARGET_LRA)}:"
        "print_format=summary"
    )


def _loudnorm_output_sample_rate() -> int:
    """Return the sample rate to apply after LoudNorm to stabilize the output."""
    return int(AUDIO_SAMPLE_RATE) if AUDIO_SAMPLE_RATE else 48000


def build_encode_audio_processing_filter(answers: dict[str, Any]) -> str:
    filters: list[str] = []
    if encode_audio_reverse_enabled(answers):
        filters.append("areverse")
    speed = encode_audio_speed_factor(answers)
    if abs(speed - 1.0) > 1e-6:
        filters.append(atempo_filter_chain(speed))
    if loudnorm_transform_enabled(answers):
        filters.append(build_loudnorm_filter(answers))
        # Explicitly resample after LoudNorm to guarantee a stable output rate.
        filters.append(f"aresample={_loudnorm_output_sample_rate()}")
    filters.append("asetpts=PTS-STARTPTS")
    chain = ",".join(filters)
    if loudnorm_transform_enabled(answers):
        log_info(f"LoudNorm audio filter segment inserted: {chain}")
        log_info(f"LoudNorm applied tracks: {answers.get('audio_tracks')}")
    return chain


def video_speed_transform_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("video_speed_enabled"))


def audio_speed_transform_enabled(answers: dict[str, Any]) -> bool:
    if answers.get("audio_speed_enabled"):
        return bool(
            answers.get("reverse_audio")
            or abs(clamp_speed_factor(answers.get("audio_speed_factor", DEFAULT_SPEED_FACTOR)) - 1.0) > 1e-6
        )
    if answers.get("audio_speed_from_video"):
        return bool(
            answers.get("reverse_video")
            or abs(encode_video_speed_factor(answers) - 1.0) > 1e-6
        )
    return False


def audio_cut_transform_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("audio_cut_keep_ranges"))


def audio_transform_enabled(answers: dict[str, Any]) -> bool:
    return audio_speed_transform_enabled(answers) or audio_cut_transform_enabled(answers) or loudnorm_transform_enabled(answers)


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
    return build_encode_audio_processing_filter(answers)


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
# Shared GUI helpers used by archived Crop/Cut helpers and active editors:
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
# the archived Cut/Crop helpers and active GUI windows. Colors inspired by
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
# The dedicated GUI lives in assets/runtime/ffmwiz_gui.py and is launched as a
# subprocess so the Qt and Tk worlds never share an event loop. Input
# and output use small JSON files via two --request / --reply CLI args.
#
# If PySide6 is not installed, _launch_qt_gui returns None. Active CLI
# workflows stay in terminal/manual mode; archived helpers may still use
# their legacy Tk implementations when called directly.
# ============================================================

FFMWIZ_RUNTIME_DIR_NAME = "runtime"
FFMWIZ_GUI_FILE_NAME = "ffmwiz_gui.py"
REQUIREMENTS_FILE_NAME = "requirements.txt"
PYSIDE6_DISPLAY_NAME = "PySide6"
PYSIDE6_PIP_SPEC = "PySide6==6.11.1"

# Module-level cache for the PySide6 availability probe so we only shell
# out once per process. Set to True/False the first time the answer is
# known. Reset to None after a successful install so we re-probe.
_PYSIDE6_AVAILABLE_CACHE: bool | None = None


def _ffmwiz_gui_path() -> Path:
    return script_dir() / "assets" / FFMWIZ_RUNTIME_DIR_NAME / FFMWIZ_GUI_FILE_NAME


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
        FFMWIZ_NO_AUTO_INSTALL=1   Skip the install prompt entirely; active
                                    GUI prompts remain unavailable.
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
        f"{PYSIDE6_DISPLAY_NAME} is not installed. The active FFmWiz graphical "
        f"editors need it for smooth playback and a professional UI."
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
            f"Skipping. FFmWiz will keep graphical editor prompts unavailable for now. "
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
            error(f"Could not run pip ({exc}). Graphical editor prompts will remain unavailable.")
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
            f"{PYSIDE6_DISPLAY_NAME} install failed. Graphical editor prompts will remain unavailable. "
            f"You can retry manually with:  py -3 -m pip install --user -r {REQUIREMENTS_FILE_NAME}"
        )
        return False

    # Re-probe so the cache picks up the newly installed package.
    _PYSIDE6_AVAILABLE_CACHE = None
    if _pyside6_available():
        note(f"{PYSIDE6_DISPLAY_NAME} installed. The new GUI is now active.")
        return True
    error(
        f"{PYSIDE6_DISPLAY_NAME} install completed but the package still cannot "
        "be imported. Graphical editor prompts will remain unavailable."
    )
    return False


def _launch_qt_gui(request: dict[str, Any]) -> dict[str, Any] | None:
    """Launch assets/runtime/ffmwiz_gui.py as a subprocess, hand it the request via a
    temp JSON file, and return the parsed reply dict.

    Returns None only when the dedicated GUI is unavailable before launch
    (missing PySide6, missing assets/runtime/ffmwiz_gui.py, etc.). Once the Qt GUI starts,
    internal GUI errors are returned as {"status": "error", ...} so callers
    do not hide real bugs behind archived fallback helpers.
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
            if result.returncode == 0:
                log_debug("Qt GUI stderr: " + result.stderr.rstrip())
            else:
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
            "%(asctime)s | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.propagate = False
        _LOGGER = logger
        log_info("FFmWiz started")
        log_info(f"Python: {sys.version.replace(chr(10), ' ')}")
        try:
            log_info(f"OS: {platform.platform()}")
        except Exception:
            pass
        log_info(f"Log file: {_LOG_PATH}")
        log_info(f"Command line: {command_to_text(sys.argv)}")
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


def command_to_text(args: Any) -> str:
    try:
        return subprocess.list2cmdline([str(arg) for arg in args])
    except Exception:
        try:
            return " ".join(str(arg) for arg in args)
        except Exception:
            return str(args)


def log_command(label: str, cmd: list[str]) -> None:
    log_info(f"{label} display command: {command_to_text(cmd)}")
    log_info(f"{label} actual argv: {json.dumps([str(part) for part in cmd], ensure_ascii=False)}")


def _compact_ffmpeg_progress_state(state: dict[str, str]) -> str:
    """Format FFmpeg -progress key/value output as one compact log line."""
    keys = [
        "frame",
        "fps",
        "stream_0_0_q",
        "bitrate",
        "total_size",
        "out_time",
        "speed",
        "progress",
    ]
    parts: list[str] = []
    seen: set[str] = set()
    for key in keys:
        value = str(state.get(key, "") or "").strip()
        if value:
            parts.append(f"{key}={value}")
            seen.add(key)
    for key, value in state.items():
        if key in seen or key.startswith("_ffmwiz_"):
            continue
        if key.endswith("_q") and key != "stream_0_0_q":
            text = str(value or "").strip()
            if text:
                parts.append(f"{key}={text}")
    return ", ".join(parts) if parts else "no progress fields"


# =====================================================================
# FFmpeg progress display.
#
# Injects -nostats -progress pipe:1 into an FFmpeg command so the
# binary writes machine-readable key=value lines to stdout while the
# console gets a single, in-place updating status line that shows
# FFmpeg-style frame/fps/q/size/time/bitrate/speed/elapsed/ETA fields.
# Raw FFmpeg stderr and compact FFmpeg progress events are captured into the
# main log file.
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
    new.extend(["-nostats", "-stats_period", "0.5", "-progress", "pipe:1", "-loglevel", "warning"])
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
    current_s = _progress_seconds_from_state(state)
    if state.get("progress") == "end" and total_duration and total_duration > 0:
        current_s = max(current_s, float(total_duration))

    def parsed_speed_ratio() -> float | None:
        raw = str(state.get("speed", "") or "").strip().lower()
        if raw.endswith("x"):
            raw = raw[:-1].strip()
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0.01 and math.isfinite(value) else None

    def q_value() -> str:
        for key in ("stream_0_0_q", "q"):
            value = state.get(key)
            if value:
                return value
        for key, value in state.items():
            if key.endswith("_q") and value:
                return value
        return "N/A"

    def visible_value(value: Any) -> str:
        return str(value or "").strip()

    def has_real_value(value: Any) -> bool:
        text = visible_value(value)
        return bool(text) and text.upper() not in {"N/A", "NA", "NONE", "NULL", "-", "-1", "-1.0"}

    def output_size() -> str:
        override = state.get("_ffmwiz_size_text")
        if override:
            return override
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
        if state.get("_ffmwiz_bitrate_text") and (
            state.get("_ffmwiz_prefer_elapsed_speed") or state.get("_ffmwiz_size_source") == "file"
        ):
            return str(state["_ffmwiz_bitrate_text"])
        if value == "N/A" and state.get("_ffmwiz_bitrate_text"):
            return str(state["_ffmwiz_bitrate_text"])
        return value

    def fps_value() -> str:
        value = state.get("fps", "N/A") or "N/A"
        return "N/A" if value in {"0", "0.0", "0.00"} else value

    def speed_value() -> str:
        override = state.get("_ffmwiz_speed_text")
        if override:
            return override
        return state.get("speed", "N/A") or "N/A"

    elapsed = max(0.0, time.perf_counter() - started_at)
    if total_duration and total_duration > 0 and current_s >= total_duration * 0.995:
        eta_s = 0.0
    elif total_duration and current_s > 0.5 and elapsed > 0.5:
        if state.get("_ffmwiz_prefer_elapsed_speed"):
            speed_ratio = current_s / elapsed
        else:
            speed_ratio = parsed_speed_ratio() or (current_s / elapsed)
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
    ]
    # Show active split part indicator if available.
    part_label = state.get("_ffmwiz_split_part_label")
    if part_label:
        verbose_segments.append((part_label, Color.LIGHT_BLUE))
    verbose_segments.append((time_total_text, ""))
    fps_text = fps_value()
    if has_real_value(fps_text):
        verbose_segments.append((f"fps {fps_text}", PROGRESS_COLORS["fps"]))
    q_text = q_value()
    if has_real_value(q_text):
        verbose_segments.append((f"q {q_text}", PROGRESS_COLORS["q"]))
    speed_text = speed_value()
    if has_real_value(speed_text):
        verbose_segments.append((f"speed {speed_text}", PROGRESS_COLORS["speed"]))
    size_text = output_size()
    if has_real_value(size_text):
        verbose_segments.append((f"size {size_text}", PROGRESS_COLORS["size"]))
    bitrate_text = bitrate_value()
    if has_real_value(bitrate_text):
        verbose_segments.append((f"bitrate {bitrate_text}", PROGRESS_COLORS["bitrate"]))
    verbose_segments.extend([
        (f"elapsed {elapsed_text}", PROGRESS_COLORS["elapsed"]),
        (eta_segment, ""),
    ])
    return _join_progress_segments(verbose_segments, colorize)


def _progress_seconds_from_state(state: dict[str, str]) -> float:
    value = state.get("_ffmwiz_current_s")
    if value:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            pass
    return _progress_raw_seconds_from_state(state)


def _progress_raw_seconds_from_state(state: dict[str, str]) -> float:
    for key in ("out_time_ms", "out_time_us"):
        value = state.get(key)
        if value:
            try:
                return max(0.0, float(value) / 1_000_000.0)
            except (TypeError, ValueError):
                pass
    text = state.get("out_time")
    if text:
        try:
            parts = text.split(":")
            if len(parts) == 3:
                return max(0.0, int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
        except (TypeError, ValueError):
            pass
    return 0.0


def _split_progress_seconds(
    raw_current_s: float,
    frame_seconds: float,
    part_durations: list[float],
    output_sizes: list[int] | None,
    previous_output_sizes: list[int] | None,
    active_part: int,
    previous_raw_s: float | None = None,
) -> tuple[float, int]:
    durations: list[float] = []
    for value in part_durations:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            durations.append(duration)
    if not durations:
        return max(0.0, max(raw_current_s, frame_seconds)), 0
    active = max(0, min(len(durations) - 1, int(active_part or 0)))
    sizes = list(output_sizes or [])
    previous_sizes = list(previous_output_sizes or [])
    growing_part: int | None = None
    max_delta = 0
    if sizes and previous_sizes:
        deltas: list[int] = []
        for idx, size in enumerate(sizes[:len(durations)]):
            previous = previous_sizes[idx] if idx < len(previous_sizes) else 0
            deltas.append(max(0, int(size) - int(previous)))
        if deltas:
            max_delta = max(deltas)
        if max_delta > 0:
            growing_part = deltas.index(max_delta)
            # Split outputs are written sequentially. Use the output file that
            # is currently growing, but never move backwards if an earlier MP4
            # part grows later while its moov atom is finalized.
            active = max(active, growing_part)
    elif previous_raw_s is not None and raw_current_s + 0.25 < previous_raw_s and active + 1 < len(durations):
        active += 1

    offset = sum(durations[:active])
    part_duration = durations[active]
    if (
        active + 1 < len(durations)
        and growing_part != active
        and frame_seconds >= offset + part_duration - 0.25
        and 0.0 < raw_current_s < part_duration - 0.25
    ):
        # Multi-output FFmpeg progress commonly freezes frame at the first
        # part's frame count while out_time restarts from zero for the next
        # output. Detect that handoff even before the next output file has
        # flushed enough bytes for file-size delta detection.
        active += 1
        offset = sum(durations[:active])
        part_duration = durations[active]
    if active == 0:
        local_s = max(raw_current_s, min(frame_seconds, part_duration))
    else:
        local_s = raw_current_s
        if local_s <= 0.0 and frame_seconds > offset:
            local_s = frame_seconds - offset
    local_s = max(0.0, min(part_duration, local_s))
    current_s = max(0.0, min(sum(durations), offset + local_s))
    return current_s, active


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


def _render_initial_progress_line(label: str, detail: str, started_at: float) -> str:
    elapsed = time.perf_counter() - started_at
    segments = [
        (label, PROGRESS_COLORS["percent"]),
        (detail, Color.GRAY),
        (f"elapsed {format_progress_elapsed_dot(elapsed)}", PROGRESS_COLORS["elapsed"]),
    ]
    return _join_progress_segments(segments, USE_COLOR)


def _progress_output_paths_from_command(cmd: list[str]) -> list[Path]:
    """Infer the final output path for simple single-output FFmpeg commands."""
    if not cmd:
        return []
    candidate = str(cmd[-1] or "").strip()
    if not candidate or candidate.startswith("-"):
        return []
    lowered = candidate.lower()
    blocked = {"-", "nul", "null", os.devnull.lower()}
    if lowered in blocked or lowered.startswith("pipe:"):
        return []
    try:
        path = Path(candidate)
    except (TypeError, ValueError):
        return []
    return [path] if path.suffix else []


def _parse_ffmpeg_bitrate_kbps(value: Any) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    multiplier = 1.0 / 1000.0
    if text.endswith("k"):
        multiplier = 1.0
        text = text[:-1].strip()
    elif text.endswith("m"):
        multiplier = 1000.0
        text = text[:-1].strip()
    try:
        parsed = float(text) * multiplier
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 and math.isfinite(parsed) else None


def _progress_target_mux_bitrate_kbps_from_command(cmd: list[str]) -> float | None:
    """Estimate the intended mux bitrate from the first video/audio -b options."""
    video_kbps: float | None = None
    audio_kbps: float | None = None
    for index, token in enumerate(cmd[:-1]):
        option = str(token or "").strip().lower()
        if not option.startswith("-b:"):
            continue
        value = _parse_ffmpeg_bitrate_kbps(cmd[index + 1])
        if value is None:
            continue
        if option.startswith("-b:v") and video_kbps is None:
            video_kbps = value
        elif option.startswith("-b:a") and audio_kbps is None:
            audio_kbps = value
    values = [value for value in (video_kbps, audio_kbps) if value is not None]
    return sum(values) if values else None


def _apply_output_file_size_progress(
    state: dict[str, str],
    output_paths: list[Path],
    current_s: float,
) -> bool:
    """Refresh progress size/bitrate from output files when FFmpeg total_size lags."""
    if not output_paths:
        return False
    total_size_bytes = 0
    for output_path in output_paths:
        try:
            if output_path.exists():
                total_size_bytes += max(0, output_path.stat().st_size)
        except OSError:
            continue
    if total_size_bytes <= 0:
        return False
    previous_size_text = state.get("_ffmwiz_size_text")
    last_size = 0
    last_size_s = 0.0
    previous_size = 0
    previous_size_s = 0.0
    try:
        last_size = int(float(state.get("_ffmwiz_last_file_size_bytes", "0") or 0))
        last_size_s = float(state.get("_ffmwiz_last_file_size_seconds", "0") or 0.0)
        previous_size = int(float(state.get("_ffmwiz_previous_file_size_bytes", "0") or 0))
        previous_size_s = float(state.get("_ffmwiz_previous_file_size_seconds", "0") or 0.0)
    except (TypeError, ValueError):
        last_size = 0
        last_size_s = 0.0
        previous_size = 0
        previous_size_s = 0.0

    if total_size_bytes > last_size:
        if last_size > 0:
            state["_ffmwiz_previous_file_size_bytes"] = str(last_size)
            state["_ffmwiz_previous_file_size_seconds"] = f"{last_size_s:.6f}"
            previous_size = last_size
            previous_size_s = last_size_s
        state["_ffmwiz_last_file_size_bytes"] = str(total_size_bytes)
        state["_ffmwiz_last_file_size_seconds"] = f"{max(0.0, current_s):.6f}"
        last_size = total_size_bytes
        last_size_s = max(0.0, current_s)

    display_size_bytes = total_size_bytes
    size_source = "file"
    if (
        state.get("progress") != "end"
        and last_size > 0
        and current_s > last_size_s + 0.01
        and total_size_bytes <= last_size
    ):
        rate_bytes_per_s: float | None = None
        if previous_size > 0 and last_size > previous_size and last_size_s > previous_size_s + 0.01:
            rate_bytes_per_s = (last_size - previous_size) / (last_size_s - previous_size_s)
        if rate_bytes_per_s is None:
            try:
                target_kbps = float(state.get("_ffmwiz_target_bitrate_kbps", "") or 0.0)
            except (TypeError, ValueError):
                target_kbps = 0.0
            if target_kbps > 0:
                rate_bytes_per_s = target_kbps * 1000.0 / 8.0
        if rate_bytes_per_s and rate_bytes_per_s > 0:
            extrapolated_seconds = min(4.0, max(0.0, current_s - last_size_s))
            estimated = int(last_size + rate_bytes_per_s * extrapolated_seconds)
            if estimated > display_size_bytes:
                display_size_bytes = estimated
                size_source = "estimated-file"
    size_text = (
        _human_size(display_size_bytes)
        .replace("KiB", "KB")
        .replace("MiB", "MB")
        .replace("GiB", "GB")
        .replace("TiB", "TB")
    )
    changed = size_text != previous_size_text
    state["_ffmwiz_size_text"] = size_text
    state["_ffmwiz_size_source"] = size_source
    if current_s > 0.001:
        bitrate_kbps = display_size_bytes * 8.0 / 1000.0 / current_s
        bitrate_text = f"{bitrate_kbps:.1f}kbits/s"
        changed = changed or bitrate_text != state.get("_ffmwiz_bitrate_text")
        state["_ffmwiz_bitrate_text"] = bitrate_text
    return changed


def run_ffmpeg_with_progress(
    cmd: list[str],
    total_duration: float | None = None,
    label: str = "FFmpeg",
    *,
    split_progress_fps: float | None = None,
    split_progress_part_durations: list[float] | None = None,
    initial_detail: str | None = None,
    progress_output_paths: list[Path] | None = None,
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
    log_info(f"{label} executed command with progress: {command_to_text(progress_cmd)}")
    log_info(f"{label} executed argv with progress: {json.dumps([str(part) for part in progress_cmd], ensure_ascii=False)}")

    started_at = time.perf_counter()
    state: dict[str, str] = {}
    target_mux_bitrate_kbps = _progress_target_mux_bitrate_kbps_from_command(cmd)
    if target_mux_bitrate_kbps:
        state["_ffmwiz_target_bitrate_kbps"] = f"{target_mux_bitrate_kbps:.6f}"
        log_info(f"{label} progress target mux bitrate estimate: {target_mux_bitrate_kbps:.1f} kbits/s")
    last_render = ""
    stderr_lines: list[str] = []
    final_emitted = False
    progress_events = 0
    output_paths = [Path(path) for path in (progress_output_paths or [])]
    if not output_paths:
        output_paths = _progress_output_paths_from_command(cmd)
    if output_paths:
        log_info(f"{label} progress output size paths: {[str(path) for path in output_paths]}")
    split_part_durations: list[float] = []
    for value in split_progress_part_durations or []:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            split_part_durations.append(duration)
    split_previous_output_sizes = [0 for _ in output_paths]
    split_active_part = 0
    split_previous_raw_s: float | None = None

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

    stdout_queue: queue.Queue[str] = queue.Queue()

    def _capture_stdout() -> None:
        try:
            for raw_line in process.stdout:  # type: ignore[union-attr]
                stdout_queue.put(raw_line)
        except Exception:
            pass

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

    stdout_thread = threading.Thread(target=_capture_stdout, daemon=True)
    stdout_thread.start()
    stderr_thread = threading.Thread(target=_capture_stderr, daemon=True)
    stderr_thread.start()
    initial_detail = initial_detail or "starting process / initializing filters / decoding first frames"
    if split_part_durations:
        initial_detail = f"Part 1/{len(split_part_durations)} — " + (initial_detail or "starting process / initializing filters / decoding first frames")
        state["_ffmwiz_split_part_label"] = f"Part 1/{len(split_part_durations)}"
    initial_render = _render_initial_progress_line(label, initial_detail, started_at)
    _write_progress_line(initial_render)
    last_render = initial_render

    try:
        while process.poll() is None or not stdout_queue.empty() or stdout_thread.is_alive():
            try:
                line = stdout_queue.get(timeout=0.25)
            except queue.Empty:
                if progress_events == 0 and split_part_durations and output_paths and total_duration and total_duration > 0:
                    # Multi-output FFmpeg may write Part 1 entirely before
                    # emitting any progress events. Estimate Part 1 progress
                    # from its output file size growth.
                    try:
                        part1_size = output_paths[0].stat().st_size if output_paths[0].exists() else 0
                    except OSError:
                        part1_size = 0
                    if part1_size > 0:
                        # Part 1 is being written. Estimate progress using the
                        # target bitrate or a linear assumption within Part 1.
                        part1_duration = split_part_durations[0]
                        target_kbps = float(state.get("_ffmwiz_target_bitrate_kbps", "0") or 0)
                        if target_kbps > 0:
                            estimated_total_bytes = target_kbps * 1000.0 / 8.0 * part1_duration
                            part1_pct = min(1.0, part1_size / max(1, estimated_total_bytes))
                        else:
                            # Without a bitrate target, assume linear write.
                            part1_pct = min(0.95, part1_size / max(1, part1_size + 1024 * 1024))
                        estimated_s = part1_pct * part1_duration
                        state["_ffmwiz_current_s"] = str(estimated_s)
                        state["_ffmwiz_prefer_elapsed_speed"] = "1"
                        state["_ffmwiz_split_part_label"] = f"Part 1/{len(split_part_durations)}"
                        elapsed_now = max(0.001, time.perf_counter() - started_at)
                        if estimated_s > 0.5:
                            state["_ffmwiz_speed_text"] = f"{estimated_s / elapsed_now:.3g}x"
                        state["_ffmwiz_size_text"] = (
                            _human_size(part1_size)
                            .replace("KiB", "KB").replace("MiB", "MB")
                            .replace("GiB", "GB").replace("TiB", "TB")
                        )
                        if estimated_s > 0.5:
                            state["_ffmwiz_bitrate_text"] = f"{part1_size * 8.0 / 1000.0 / estimated_s:.1f}kbits/s"
                        rendered = _render_progress_line(state, total_duration, started_at)
                        _write_progress_line(rendered)
                        last_render = rendered
                    else:
                        last_render = _render_initial_progress_line(label, initial_detail, started_at)
                        _write_progress_line(last_render)
                elif progress_events == 0:
                    last_render = _render_initial_progress_line(label, initial_detail, started_at)
                    _write_progress_line(last_render)
                elif state:
                    current_s = _progress_seconds_from_state(state)
                    _apply_output_file_size_progress(state, output_paths, current_s)
                    rendered = _render_progress_line(state, total_duration, started_at)
                    _write_progress_line(rendered)
                    last_render = rendered
                continue
            line = line.strip()
            if "=" not in line:
                if line:
                    log_debug(f"{label} stdout: {line}")
                continue
            key, _, value = line.partition("=")
            state[key.strip()] = value.strip()
            if key.strip() != "progress":
                continue
            log_debug(f"{label} stdout progress: {_compact_ffmpeg_progress_state(state)}")
            progress_events += 1
            raw_current_s = _progress_raw_seconds_from_state(state)
            current_s = raw_current_s
            if split_progress_fps and split_progress_fps > 0:
                frame_text = str(state.get("frame", "") or "").strip()
                try:
                    frame_seconds = max(0.0, float(frame_text) / float(split_progress_fps))
                except (TypeError, ValueError):
                    frame_seconds = 0.0
                output_sizes: list[int] = []
                if output_paths:
                    for output_path in output_paths:
                        try:
                            output_sizes.append(output_path.stat().st_size)
                        except OSError:
                            output_sizes.append(0)
                if split_part_durations:
                    current_s, split_active_part = _split_progress_seconds(
                        raw_current_s,
                        frame_seconds,
                        split_part_durations,
                        output_sizes,
                        split_previous_output_sizes,
                        split_active_part,
                        split_previous_raw_s,
                    )
                    split_previous_output_sizes = output_sizes
                    split_previous_raw_s = raw_current_s
                    # Show which part is currently encoding.
                    state["_ffmwiz_split_part_label"] = f"Part {split_active_part + 1}/{len(split_part_durations)}"
                elif frame_seconds > 0.0:
                    # Fallback for callers that only provide FPS. This avoids
                    # double-counting but cannot infer later Split parts.
                    current_s = max(raw_current_s, frame_seconds)
                if current_s > 0.0:
                    if total_duration and total_duration > 0:
                        current_s = min(current_s, float(total_duration))
                    state["_ffmwiz_prefer_elapsed_speed"] = "1"
                    elapsed_now = max(0.001, time.perf_counter() - started_at)
                    if current_s > 0.001:
                        aggregate_speed = current_s / elapsed_now
                        state["_ffmwiz_speed_text"] = f"{aggregate_speed:.3g}x"
                        total_size_bytes = sum(output_sizes)
                        total_size_text = str(state.get("total_size", "") or "").strip()
                        if total_size_bytes > 0:
                            state["_ffmwiz_size_text"] = (
                                _human_size(total_size_bytes)
                                .replace("KiB", "KB")
                                .replace("MiB", "MB")
                                .replace("GiB", "GB")
                                .replace("TiB", "TB")
                            )
                            bitrate_kbps = total_size_bytes * 8.0 / 1000.0 / current_s
                            state["_ffmwiz_bitrate_text"] = f"{bitrate_kbps:.1f}kbits/s"
                        elif total_size_text.isdigit():
                            bitrate_kbps = int(total_size_text) * 8.0 / 1000.0 / current_s
                            state["_ffmwiz_bitrate_text"] = f"{bitrate_kbps:.1f}kbits/s"
            previous_s = float(state.get("_ffmwiz_current_s", "0") or 0.0)
            state["_ffmwiz_current_s"] = str(max(previous_s, current_s))
            _apply_output_file_size_progress(state, output_paths, max(previous_s, current_s))
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
    stdout_thread.join(timeout=2.0)
    stderr_thread.join()
    elapsed = time.perf_counter() - started_at

    if not final_emitted:
        _finish_progress_line(last_render or None)
    log_debug(f"{label} progress parser events: {progress_events}")
    log_info(f"{label} raw FFmpeg progress/stderr captured in the main log; no stdout/stderr sidecar files were created.")

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
    # Round to the NEAREST even number (codecs need even dimensions). Nearest-even
    # keeps the computed edge as close as possible to the ideal aspect-ratio-
    # preserving value, instead of always rounding up (which skewed the AR).
    return max(2, int(round(float(value) / 2.0)) * 2)


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


def parse_sar_value(sar_str: str | None) -> float:
    """Parse a sample aspect ratio string (e.g. '4:3', '16/9', '1.333') to a float.
    Returns 1.0 for unknown, empty, or invalid values."""
    if not sar_str or str(sar_str).strip().lower() in {"", "n/a", "unknown", "0:0", "0/0", "0:1"}:
        return 1.0
    text = str(sar_str).strip()
    # Handle ratio notation: "N:M" or "N/M"
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)", text)
    if match:
        num = float(match.group(1))
        den = float(match.group(2))
        if den > 0:
            return num / den
        return 1.0
    # Handle plain float
    try:
        val = float(text)
        return val if val > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def source_sar(answers: dict[str, Any]) -> float:
    """Return the source video sample aspect ratio as a float (>0). Default 1.0."""
    stream = answers.get("video_streams", [{}])[0] if answers.get("video_streams") else {}
    sar_str = stream.get("sample_aspect_ratio")
    return parse_sar_value(sar_str)


def cropped_display_size(answers: dict[str, Any]) -> tuple[int, int]:
    """Return the effective display dimensions after crop, accounting for SAR.
    These are the dimensions as seen on screen (DAR-adjusted)."""
    crop_w, crop_h = cropped_source_size(answers)
    sar = source_sar(answers)
    if abs(sar - 1.0) < 0.001:
        return crop_w, crop_h
    # SAR > 1 means pixels are wider than tall → display width is larger.
    display_w = crop_w * sar
    display_h = float(crop_h)
    return max(2, even_dimension(display_w)), max(2, even_dimension(display_h))


def resize_mode_is_stretch(answers: dict[str, Any]) -> bool:
    """Return True if the user explicitly selected a stretch/exact mode."""
    resolution = answers.get("resolution", "n")
    if isinstance(resolution, dict):
        return resolution.get("mode") == "exact_stretch"
    if isinstance(resolution, tuple) and len(resolution) == 2:
        return True  # Legacy tuple mode is always stretch.
    return False


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

    # Use display dimensions (accounting for SAR) for AR-preserving modes.
    # Output pixels are square (setsar=1), so scaled dimensions must reflect
    # the display aspect ratio, not the coded pixel grid.
    sar = source_sar(answers)
    disp_w, disp_h = cropped_display_size(answers)

    warning_parts: list[str] = []
    axis = ""
    if isinstance(resolution, dict):
        mode = resolution.get("mode")
        if mode == "preset":
            # Preset mode: compute AR-preserving dimensions that fit within
            # the preset box. The scale+pad filter handles final canvas.
            width, height, axis = closest_edge_scale_dimensions(
                disp_w,
                disp_h,
                int(resolution.get("width", disp_w) or disp_w),
                int(resolution.get("height", disp_h) or disp_h),
            )
        elif mode == "box":
            # Box mode: target the exact requested canvas dimensions.
            # The scale filter uses force_original_aspect_ratio=decrease to
            # fit the content, then pad fills the canvas. This ensures the
            # output is exactly the requested size without distortion.
            width = even_dimension(int(resolution.get("width", disp_w) or disp_w))
            height = even_dimension(int(resolution.get("height", disp_h) or disp_h))
            axis = "box"
        elif mode == "height":
            height = even_dimension(resolution.get("height", disp_h))
            width = even_dimension(height * disp_w / max(1, disp_h))
            axis = "height"
        elif mode == "width":
            width = even_dimension(resolution.get("width", disp_w))
            height = even_dimension(width * disp_h / max(1, disp_w))
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
            crop_ar = disp_w / max(1, disp_h)
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
    log_info(
        "Resolution calculation: "
        f"source={first_video_size(answers)}; SAR={sar:.4f}; "
        f"crop_margins={format_crop_margins(answers)}; "
        f"cropped_coded={crop_w}x{crop_h}; cropped_display={disp_w}x{disp_h}; "
        f"mode={resolution}; axis={axis}; "
        f"final={width}x{height}; exact_stretch={'yes' if axis == 'stretch' else 'no'}"
    )
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
        "-show_chapters",
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
        if os.environ.get("FFMWIZ_DEBUG"):
            try:
                safe_name = sanitize_output_stem(input_path.name)
                stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
                json_path = _logs_dir() / f"ffprobe_{stamp}_{safe_name}.json"
                json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                log_info(f"Full ffprobe JSON saved to: {json_path}")
            except Exception as exc:
                log_warn(f"Could not save ffprobe JSON debug file: {exc}")
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
        "-hide_banner",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-show_programs",
        "-show_private_data",
        "-print_format",
        "json",
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


VOLUMEDETECT_RE = re.compile(r"\b(mean_volume|max_volume):\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*dB")


def parse_volumedetect_output(text: str) -> dict[str, str]:
    stats: dict[str, str] = {}
    for key, value in VOLUMEDETECT_RE.findall(text or ""):
        stats[key] = f"{value} dB"
    return stats


def parse_loudnorm_measurement_output(text: str) -> dict[str, float] | None:
    for match in reversed(list(re.finditer(r"\{[\s\S]*?\}", text or ""))):
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        if not all(key in payload for key in required):
            continue
        try:
            values = {key: float(payload[key]) for key in required}
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in values.values()):
            return values
    return None


def probe_loudnorm_measurement(
    ffmpeg: str,
    input_path: Path,
    audio_index: int,
    target_i: float = LOUDNORM_DEFAULT_TARGET_I,
    total_duration: float | None = None,
) -> dict[str, float] | None:
    loudnorm = (
        f"loudnorm=I={loudnorm_number(target_i)}:"
        f"TP={loudnorm_number(LOUDNORM_TARGET_TP)}:"
        f"LRA={loudnorm_number(LOUDNORM_TARGET_LRA)}:"
        "print_format=json"
    )
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-stats_period",
        "0.5",
        "-progress",
        "pipe:1",
        "-i",
        str(input_path),
        "-map",
        f"0:a:{int(audio_index)}",
        "-vn",
        "-sn",
        "-dn",
        "-af",
        loudnorm,
        "-f",
        "null",
        os.devnull,
    ]
    log_info("LoudNorm measurement command: " + command_to_powershell(args))
    log_command("LoudNorm measurement", args)
    started_at = time.perf_counter()
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    state: dict[str, str] = {}
    log_info(
        "LoudNorm measurement uses null output; size/bitrate progress fields "
        "are omitted unless FFmpeg reports real output values."
    )
    last_render = ""
    progress_events = 0
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        def _capture_stderr() -> None:
            try:
                for line in process.stderr:  # type: ignore[union-attr]
                    stripped = line.rstrip()
                    if stripped:
                        stderr_lines.append(stripped)
                        log_debug(f"LoudNorm measurement stderr: {stripped}")
            except Exception:
                pass

        stderr_thread = threading.Thread(target=_capture_stderr, daemon=True)
        stderr_thread.start()
        initial_render = paint("LoudNorm measurement: analyzing audio loudness...", Color.GRAY) if USE_COLOR else "LoudNorm measurement: analyzing audio loudness..."
        _write_progress_line(initial_render)
        last_render = initial_render
        for line in process.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if line:
                stdout_lines.append(line)
            if "=" not in line:
                if line:
                    log_debug(f"LoudNorm measurement stdout: {line}")
                continue
            key, _, value = line.partition("=")
            state[key.strip()] = value.strip()
            if key.strip() != "progress":
                continue
            log_debug(f"LoudNorm measurement stdout progress: {_compact_ffmpeg_progress_state(state)}")
            progress_events += 1
            current_s = _progress_raw_seconds_from_state(state)
            previous_s = float(state.get("_ffmwiz_current_s", "0") or 0.0)
            state["_ffmwiz_current_s"] = str(max(previous_s, current_s))
            rendered = _render_progress_line(state, total_duration, started_at)
            _write_progress_line(rendered)
            last_render = rendered
            if value.strip() == "end":
                _finish_progress_line(rendered)
                last_render = ""
        process.wait()
        stderr_thread.join()
        if last_render:
            _finish_progress_line(last_render)
        combined = "\n".join(stderr_lines + stdout_lines)
        stats = parse_loudnorm_measurement_output(combined)
        log_info(
            "LoudNorm measurement finished: "
            f"input={input_path}; audio_index={audio_index}; returncode={process.returncode}; "
            f"elapsed={time.perf_counter() - started_at:.3f}s; "
            f"progress_events={progress_events}; stats={stats or '{}'}"
        )
        if process.returncode != 0 or stats is None:
            log_error("LoudNorm measurement output:\n" + _text_preview(combined, 4000))
        return stats
    except Exception:
        log_exception(f"LoudNorm measurement failed: input={input_path}; audio_index={audio_index}")
        return None


def print_loudnorm_stats(stats: dict[str, float]) -> None:
    print()
    print(paint("Current audio loudnorm measurement:", Color.BOLD + Color.LIGHT_BLUE))
    print("  " + field_text("Integrated loudness", f"{stats['input_i']:.1f} LUFS", Color.MEAN_VOLUME))
    print("  " + field_text("True peak", f"{stats['input_tp']:.1f} dBTP", Color.CYAN))
    print("  " + field_text("Loudness range", f"{stats['input_lra']:.1f} LU", Color.MAGENTA))
    print("  " + field_text("Threshold", f"{stats['input_thresh']:.1f} LUFS", Color.YELLOW))
    print("  " + field_text("Target offset", f"{stats['target_offset']:.1f} LU", Color.ORANGE))


def probe_audio_volume_stats(ffmpeg: str, input_path: Path, audio_index: int) -> dict[str, str]:
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        str(input_path),
        "-map",
        f"0:a:{int(audio_index)}",
        "-vn",
        "-sn",
        "-dn",
        "-af",
        "volumedetect",
        "-f",
        "null",
        os.devnull,
    ]
    started_at = time.perf_counter()
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout_text, stdout_encoding = decode_subprocess_bytes(result.stdout, "utf-8")
        stderr_text, stderr_encoding = decode_subprocess_bytes(result.stderr, "utf-8")
        combined = stderr_text + "\n" + stdout_text
        stats = parse_volumedetect_output(combined)
        log_debug(
            "Audio volume scan: "
            f"input={input_path}; audio_index={audio_index}; returncode={result.returncode}; "
            f"elapsed={time.perf_counter() - started_at:.3f}s; "
            f"decoded=stdout:{stdout_encoding},stderr:{stderr_encoding}; stats={stats or '{}'}"
        )
        if result.returncode != 0 and not stats:
            log_debug("Audio volume scan stderr:\n" + stderr_text.strip())
        return stats
    except Exception:
        log_exception(f"Audio volume scan failed: input={input_path}; audio_index={audio_index}")
        return {}


def get_audio_volume_stats(answers: dict[str, Any]) -> dict[int, dict[str, str]]:
    if "audio_volume_stats" in answers:
        return answers["audio_volume_stats"]
    ffmpeg = answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"
    input_path = answers.get("input_path")
    streams = list(answers.get("audio_streams") or [])
    stats: dict[int, dict[str, str]] = {}
    if input_path and streams:
        worker_count = min(VOLUME_SCAN_WORKERS, len(streams))
        log_debug(
            f"Audio volume scan batch: input={input_path}; streams={len(streams)}; workers={worker_count}"
        )
        if worker_count > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {
                    executor.submit(probe_audio_volume_stats, str(ffmpeg), Path(input_path), idx): idx
                    for idx, _stream in enumerate(streams)
                }
                for future in concurrent.futures.as_completed(future_map):
                    idx = future_map[future]
                    try:
                        stats[idx] = future.result()
                    except Exception:
                        log_exception(f"Audio volume scan worker failed: input={input_path}; audio_index={idx}")
                        stats[idx] = {}
        else:
            for idx, _stream in enumerate(streams):
                stats[idx] = probe_audio_volume_stats(str(ffmpeg), Path(input_path), idx)
    answers["audio_volume_stats"] = stats
    return stats


def audio_volume_field(stats: dict[int, dict[str, str]], index: int, key: str) -> str:
    value = (stats.get(index) or {}).get(key)
    return value or "unknown"


def audio_mean_max_volume_field(stats: dict[int, dict[str, str]], index: int) -> str:
    mean_value = audio_volume_field(stats, index, "mean_volume")
    max_value = audio_volume_field(stats, index, "max_volume")
    if mean_value.endswith(" dB") and max_value.endswith(" dB"):
        return f"{mean_value[:-3]} / {max_value[:-3]} dB"
    return f"{mean_value} / {max_value}"


def stream_tag_size_bytes(stream: dict[str, Any]) -> int | None:
    return tag_int(stream, ["NUMBER_OF_BYTES", "NUMBER_OF_BYTES-ENG"])


def stream_statistics_siblings(stream: dict[str, Any], sibling_streams: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    streams = [item for item in (sibling_streams or []) if isinstance(item, dict)]
    stream_index = stream.get("index")
    if stream_index is not None and all(item.get("index") != stream_index for item in streams):
        streams.append(stream)
    elif stream_index is None and not streams:
        streams.append(stream)
    return streams


def stream_statistics_tags_conflict_with_container(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> bool:
    total_size = format_size_bytes_from_metadata(fmt)
    if not total_size:
        return False
    streams = stream_statistics_siblings(stream, sibling_streams)
    tagged_sizes: list[int] = []
    for item in streams:
        size = stream_tag_size_bytes(item)
        if size is None or size <= 0:
            continue
        if size > int(total_size * 1.02):
            return True
        tagged_sizes.append(size)
    if len(tagged_sizes) > 1 and sum(tagged_sizes) > int(total_size * 1.02):
        return True
    return False


def stream_statistics_tags_trustworthy(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> bool:
    size_bytes = stream_tag_size_bytes(stream)
    if size_bytes is not None and not stream_size_plausible(size_bytes, fmt):
        return False
    if stream_statistics_tags_conflict_with_container(stream, fmt, sibling_streams):
        return False
    return True


def stream_size_plausible(size_bytes: int | None, fmt: dict[str, Any] | None) -> bool:
    if size_bytes is None or size_bytes <= 0:
        return False
    total_size = format_size_bytes_from_metadata(fmt)
    if total_size and size_bytes > int(total_size * 1.02):
        return False
    return True


def stream_bitrate_plausible(kbps: int | None, stream: dict[str, Any] | None,
                             fmt: dict[str, Any] | None) -> bool:
    if kbps is None or kbps <= 0:
        return False
    duration = stream_duration_seconds(stream or {}, fmt)
    total_size = format_size_bytes_from_metadata(fmt)
    if duration and total_size:
        implied_size = kbps * 1000 * duration / 8
        if implied_size > total_size * 1.02:
            return False
    return True


def stream_has_fast_size_metadata(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> bool:
    return stream_size_plausible(stream_tag_size_bytes(stream), fmt) and stream_statistics_tags_trustworthy(stream, fmt, sibling_streams)


def packet_size_probe_needed(answers: dict[str, Any]) -> bool:
    fmt = answers.get("format", {})
    streams = list(answers.get("video_streams", [])) + list(answers.get("audio_streams", []))
    if not streams:
        return False
    total_size = format_size_bytes_from_metadata(fmt)
    tag_sizes = [stream_tag_size_bytes(stream) for stream in streams]
    if total_size and all(size and size > 0 for size in tag_sizes):
        if sum(int(size or 0) for size in tag_sizes) > int(total_size * 1.02):
            return True
    return any(not stream_has_fast_size_metadata(stream, fmt, streams) for stream in streams)


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


def bitrate_kbps(
    stream: dict[str, Any] | None,
    fmt: dict[str, Any] | None = None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> int | None:
    sources = (("stream", stream),) if stream is not None else (("format", fmt),)
    for source_name, source in sources:
        if not source:
            continue
        bit_rate = source.get("bit_rate")
        if bit_rate:
            try:
                value = max(1, round(int(bit_rate) / 1000))
                if source_name == "format" or stream_bitrate_plausible(value, stream, fmt):
                    return value
            except ValueError:
                pass
        tags = source.get("tags") if isinstance(source, dict) else None
        if isinstance(tags, dict):
            normalized = {str(key).upper(): value for key, value in tags.items()}
            for key in ("BPS", "BPS-ENG"):
                tagged_bps = normalized.get(key)
                if tagged_bps:
                    try:
                        value = max(1, round(int(float(tagged_bps)) / 1000))
                        if source_name == "format" or (
                            stream_bitrate_plausible(value, stream, fmt)
                            and stream is not None
                            and stream_statistics_tags_trustworthy(stream, fmt, sibling_streams)
                        ):
                            return value
                    except (TypeError, ValueError):
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
        direct = bitrate_kbps(other, fmt, siblings)
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
    stream_index = stream.get("index")
    if packet_sizes and stream_index in packet_sizes:
        return packet_sizes[stream_index], False

    exact = stream_tag_size_bytes(stream)
    if stream_size_plausible(exact, fmt) and stream_statistics_tags_trustworthy(stream, fmt, sibling_streams):
        return exact, False

    duration = stream_duration_seconds(stream, fmt)
    rate = bitrate_kbps(stream, fmt, sibling_streams)
    if os.environ.get("FFMWIZ_ALLOW_ESTIMATED_STREAM_SIZES") and duration and rate:
        return round(rate * 1000 * duration / 8), True
    return None, True


def stream_bitrate_kbps(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None = None,
    packet_sizes: dict[int, int] | None = None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> int | None:
    stream_index = stream.get("index")
    duration = stream_duration_seconds(stream, fmt)
    if packet_sizes and stream_index in packet_sizes and duration:
        return max(1, round(packet_sizes[stream_index] * 8 / duration / 1000))
    direct = bitrate_kbps(stream, fmt, sibling_streams)
    if direct:
        return direct
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
    sibling_streams = streams_for_statistics_from_answers(answers)
    for idx, stream in enumerate(answers["audio_streams"]):
        size, _ = stream_size_bytes(stream, fmt, packet_sizes, sibling_streams)
        volume_stats = get_audio_volume_stats(answers)
        labels = duplicate_labels(idx, report)
        suffix = f" | {' | '.join(labels)}" if labels else ""
        print(
            f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
            f"{field_text('stream', '#' + str(stream.get('index')), Color.WHITE)} | "
            f"{field_text('codec', stream_metadata_value(stream, 'codec_name'), Color.CYAN)} | "
            f"{field_text('sample_rate', stream_metadata_value(stream, 'sample_rate'), Color.GREEN)} | "
            f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
            f"{field_text('layout', stream_metadata_value(stream, 'channel_layout'), Color.WHITE)} | "
            f"{field_text('bitrate', describe_bitrate(stream_bitrate_kbps(stream, fmt, packet_sizes, sibling_streams)), Color.YELLOW)} | "
            f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, idx), Color.MEAN_VOLUME)} | "
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
    title = str(answers.get("_source_info_title") or "Source file info")
    sibling_streams = streams_for_statistics_from_answers(answers)

    print()
    print(paint(title, Color.BOLD + Color.LIGHT_BLUE))
    print(paint("-" * 48, Color.GRAY))
    print(field_text("Path", input_path, Color.WHITE))
    print(field_text("Container", fmt.get("format_name", "unknown"), Color.CYAN))
    print(field_text("Duration", format_duration(stream_duration_seconds({}, fmt)), Color.MAGENTA))
    print(field_text("File size", format_bytes(file_size), Color.LIME))
    print(field_text("Total bitrate", describe_total_bitrate(fmt), Color.YELLOW))

    if video_streams:
        print(paint("\nVideo streams", Color.BOLD + Color.MAGENTA))
        chapters_value, chapters_color = chapter_presence(answers)
        for idx, stream in enumerate(video_streams):
            rate = stream_bitrate_kbps(stream, fmt, packet_sizes, sibling_streams)
            size, estimated = stream_size_bytes(stream, fmt, packet_sizes, sibling_streams)
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
                f"{field_text('chapters', chapters_value, chapters_color)} | "
                f"{field_text('Color range', display_color_range(stream.get('color_range')), Color.COLOR_RANGE_VALUE)}"
            )

    if audio_streams:
        print(paint("\nAudio streams", Color.BOLD + Color.BLUE))
        volume_stats = get_audio_volume_stats(answers)
        report = detect_duplicate_audio(answers) if answers.get("detect_duplicate_audio", True) else None
        for idx, stream in enumerate(audio_streams):
            rate = stream_bitrate_kbps(stream, fmt, packet_sizes, sibling_streams)
            size, estimated = stream_size_bytes(stream, fmt, packet_sizes, sibling_streams)
            estimate_label = " approx" if estimated and size else ""
            labels = duplicate_labels(idx, report) if report else []
            label_text = f" | {' | '.join(labels)}" if labels else ""
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
                f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, idx), Color.MEAN_VOLUME)} | "
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


@dataclass
class MediaInfoOptions:
    deep_analysis: bool = True
    extract_screenshots: bool = False
    reference_path: Path | None = None


@dataclass
class MediaInfoReportResult:
    lines: list[tuple[str, str]]
    info_path: Path
    sidecar_paths: list[Path] = field(default_factory=list)
    screenshot_dir: Path | None = None
    skipped_sections: list[str] = field(default_factory=list)


def media_info_sidecar_path(info_path: Path, suffix: str, extension: str) -> Path:
    extension = extension if extension.startswith(".") else "." + extension
    return info_path.with_name(f"{info_path.stem}_{suffix}{extension}")


def media_info_stream_name(stream: dict[str, Any]) -> str:
    index = stream.get("index", "?")
    codec_type = stream.get("codec_type", "unknown")
    codec = stream.get("codec_name", "unknown")
    return f"stream #{index} {codec_type} {codec}"


def media_info_stream_tags(stream: dict[str, Any]) -> dict[str, Any]:
    tags = stream.get("tags")
    return tags if isinstance(tags, dict) else {}


def media_info_disposition(stream: dict[str, Any], name: str) -> str:
    disposition = stream.get("disposition")
    if not isinstance(disposition, dict):
        return "unknown"
    value = disposition.get(name)
    if value in {1, "1", True}:
        return "yes"
    if value in {0, "0", False}:
        return "no"
    return "unknown"


def media_info_video_fps(stream: dict[str, Any]) -> float | None:
    return rational_to_float(stream.get("avg_frame_rate")) or rational_to_float(stream.get("r_frame_rate"))


def media_info_stream_size_rows(
    input_path: Path,
    payload: dict[str, Any],
    packet_sizes: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    total_size = input_path.stat().st_size if input_path.exists() else format_size_bytes_from_metadata(fmt)
    rows: list[dict[str, Any]] = []
    for stream in streams:
        tags = media_info_stream_tags(stream)
        size_bytes, estimated = stream_size_bytes(stream, fmt, packet_sizes, streams)
        bitrate = stream_bitrate_kbps(stream, fmt, packet_sizes, streams)
        percent = (size_bytes / total_size * 100.0) if size_bytes is not None and total_size else None
        rows.append({
            "stream_index": stream.get("index", "unknown"),
            "type": stream.get("codec_type", "unknown"),
            "codec": stream.get("codec_name", "unknown"),
            "language": display_language(tags.get("language")),
            "title": tags.get("title") or "unknown",
            "duration": format_duration(stream_duration_seconds(stream, fmt)),
            "bitrate": describe_bitrate(bitrate),
            "size_bytes": size_bytes if size_bytes is not None else "unknown",
            "size_mb": f"{size_bytes / (1024 * 1024):.2f}" if size_bytes is not None else "unknown",
            "percent_of_file": f"{percent:.2f}" if percent is not None else "unknown",
            "estimated": "yes" if estimated else "no",
        })
    return rows


def write_media_info_stream_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "stream_index", "type", "codec", "language", "title", "duration",
        "bitrate", "size_bytes", "size_mb", "percent_of_file", "estimated",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def calculate_bpppf_rows(payload: dict[str, Any], packet_sizes: dict[int, int] | None = None) -> list[dict[str, Any]]:
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    rows: list[dict[str, Any]] = []
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        width = int_metadata_value(stream, "width")
        height = int_metadata_value(stream, "height")
        fps = media_info_video_fps(stream)
        bitrate = stream_bitrate_kbps(stream, fmt, packet_sizes, streams)
        bpppf = None
        if width and height and fps and bitrate:
            bpppf = (bitrate * 1000.0) / (width * height * fps)
        rows.append({
            "stream_index": stream.get("index", "unknown"),
            "width": width or "unknown",
            "height": height or "unknown",
            "fps": f"{fps:.5g}" if fps else "unknown",
            "video_bitrate": describe_bitrate(bitrate),
            "bpppf": f"{bpppf:.6f}" if bpppf is not None else "unknown",
        })
    return rows


def media_info_percentile(values: list[float], percentile: float) -> float | None:
    cleaned = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    position = (len(cleaned) - 1) * percentile / 100.0
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return cleaned[lower]
    fraction = position - lower
    return cleaned[lower] * (1.0 - fraction) + cleaned[upper] * fraction


def run_media_info_text_command(args: list[str], label: str) -> tuple[int, str, str]:
    log_debug(f"{label} command: {json.dumps(args, ensure_ascii=False)}")
    result = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    stdout_text, stdout_encoding = decode_subprocess_bytes(result.stdout, "utf-8")
    stderr_text, stderr_encoding = decode_subprocess_bytes(result.stderr, "utf-8")
    log_debug(
        f"{label} returncode={result.returncode}; "
        f"stdout_encoding={stdout_encoding}; stderr_encoding={stderr_encoding}; "
        f"stdout_len={len(stdout_text)}; stderr_len={len(stderr_text)}"
    )
    if stderr_text.strip():
        log_debug(f"{label} stderr:\n{stderr_text.rstrip()}")
    return result.returncode, stdout_text, stderr_text


def analyze_packet_bitrate(ffprobe: str, input_path: Path, csv_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    args = [
        ffprobe,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_packets",
        "-show_entries", "packet=pts_time,dts_time,duration_time,size,flags",
        "-of", "csv=p=0",
        str(input_path),
    ]
    returncode, stdout_text, _stderr_text = run_media_info_text_command(args, "Media Info per-second bitrate analysis")
    if returncode != 0:
        return None, "ffprobe packet analysis failed"
    buckets: dict[int, int] = {}
    for row in csv.reader(stdout_text.splitlines()):
        if not row:
            continue
        timestamp: float | None = None
        for item in row[:2]:
            try:
                timestamp = float(item)
                break
            except (TypeError, ValueError):
                continue
        size: int | None = None
        for item in row:
            try:
                number = int(float(item))
            except (TypeError, ValueError):
                continue
            if number > 1:
                size = number
        if timestamp is None or size is None:
            continue
        second = max(0, int(math.floor(timestamp)))
        buckets[second] = buckets.get(second, 0) + size
    if not buckets:
        return None, "packet timestamps or sizes were not available"
    rows = [
        {"second": second, "kbps": bytes_value * 8.0 / 1000.0, "bytes": bytes_value}
        for second, bytes_value in sorted(buckets.items())
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["second", "kbps", "bytes"])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "second": row["second"],
                "kbps": f"{row['kbps']:.3f}",
                "bytes": row["bytes"],
            })
    kbps_values = [float(row["kbps"]) for row in rows]
    summary = {
        "csv_path": csv_path,
        "rows": rows,
        "average_kbps": sum(kbps_values) / len(kbps_values),
        "minimum_kbps": min(kbps_values),
        "maximum_kbps": max(kbps_values),
        "p05_kbps": media_info_percentile(kbps_values, 5),
        "median_kbps": media_info_percentile(kbps_values, 50),
        "p95_kbps": media_info_percentile(kbps_values, 95),
        "seconds": len(rows),
    }
    return summary, None


def media_info_csv_time(value: str) -> float | None:
    try:
        if value and value.upper() != "N/A":
            return float(value)
    except (TypeError, ValueError):
        return None
    return None


def analyze_frame_types_and_gop(ffprobe: str, input_path: Path, csv_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    args = [
        ffprobe,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time,pkt_pts_time,pict_type,key_frame,pkt_size",
        "-of", "csv=p=0",
        str(input_path),
    ]
    returncode, stdout_text, _stderr_text = run_media_info_text_command(args, "Media Info frame type analysis")
    if returncode != 0:
        return None, "ffprobe frame analysis failed"
    frame_rows: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {"I": 0, "P": 0, "B": 0}
    keyframe_indices: list[int] = []
    keyframe_times: list[float] = []
    for row in csv.reader(stdout_text.splitlines()):
        if not row:
            continue
        pict_type = next((item.strip().upper() for item in row if item.strip().upper() in {"I", "P", "B"}), "unknown")
        key_frame = "1" if any(item.strip() == "1" for item in row[:3]) else "0"
        time_value = next((media_info_csv_time(item) for item in row if media_info_csv_time(item) is not None), None)
        pkt_size = None
        for item in reversed(row):
            try:
                number = int(float(item))
            except (TypeError, ValueError):
                continue
            if number > 1:
                pkt_size = number
                break
        frame_index = len(frame_rows)
        if pict_type in type_counts:
            type_counts[pict_type] += 1
        if key_frame == "1":
            keyframe_indices.append(frame_index)
            if time_value is not None:
                keyframe_times.append(time_value)
        frame_rows.append({
            "time": f"{time_value:.6f}" if time_value is not None else "",
            "pict_type": pict_type,
            "key_frame": key_frame,
            "pkt_size": pkt_size if pkt_size is not None else "",
        })
    if not frame_rows:
        return None, "no frame rows were available"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time", "pict_type", "key_frame", "pkt_size"])
        writer.writeheader()
        writer.writerows(frame_rows)
    total = len(frame_rows)
    gop_lengths = [
        keyframe_indices[index] - keyframe_indices[index - 1]
        for index in range(1, len(keyframe_indices))
    ]
    keyframe_intervals = [
        keyframe_times[index] - keyframe_times[index - 1]
        for index in range(1, len(keyframe_times))
        if keyframe_times[index] >= keyframe_times[index - 1]
    ]
    summary = {
        "csv_path": csv_path,
        "total_frames": total,
        "i_frames": type_counts.get("I", 0),
        "p_frames": type_counts.get("P", 0),
        "b_frames": type_counts.get("B", 0),
        "keyframes": len(keyframe_indices),
        "average_gop_frames": (sum(gop_lengths) / len(gop_lengths)) if gop_lengths else None,
        "minimum_gop_frames": min(gop_lengths) if gop_lengths else None,
        "maximum_gop_frames": max(gop_lengths) if gop_lengths else None,
        "first_keyframe_time": keyframe_times[0] if keyframe_times else None,
        "last_keyframe_time": keyframe_times[-1] if keyframe_times else None,
        "average_keyframe_interval": (sum(keyframe_intervals) / len(keyframe_intervals)) if keyframe_intervals else None,
        "minimum_keyframe_interval": min(keyframe_intervals) if keyframe_intervals else None,
        "maximum_keyframe_interval": max(keyframe_intervals) if keyframe_intervals else None,
    }
    return summary, None


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


def info_stream_header(stream: dict[str, Any], relative_index: int, chapter_count: int = 0) -> str:
    codec_type = stream.get("codec_type", "unknown")
    codec_name = stream.get("codec_name", "unknown")
    global_index = stream.get("index", "?")
    title = stream_tag_value(stream, "title", "")
    language = display_language(stream_tag_value(stream, "language", ""))
    suffix = []
    if codec_type == "video":
        suffix.append(f"bit_depth={describe_video_bit_depth(stream)}")
        suffix.append(f"Color range={display_color_range(stream.get('color_range'))}")
        suffix.append(f"chapters={'yes' if chapter_count else 'no'}")
    if language:
        suffix.append(f"language={language}")
    if title:
        suffix.append(f"title={title}")
    suffix_text = " | " + " | ".join(suffix) if suffix else ""
    return f"Stream {relative_index} / #{global_index}: {codec_type} | codec={codec_name}{suffix_text}"


def media_info_video_codec_family(codec: str) -> str:
    normalized = str(codec or "").lower()
    if normalized in {"h264", "avc1"}:
        return "H.264/AVC"
    if normalized in {"hevc", "h265", "hev1", "hvc1"}:
        return "H.265/HEVC"
    if normalized == "av1":
        return "AV1"
    if normalized in {"vp9", "vp8"}:
        return normalized.upper()
    return codec or "unknown"


def media_info_audio_coding_type(codec: str) -> str:
    normalized = str(codec or "").lower()
    if normalized in {"flac", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "truehd"}:
        return "lossless"
    if normalized:
        return "lossy or compressed"
    return "unknown"


def media_info_subtitle_kind(codec: str) -> str:
    normalized = str(codec or "").lower()
    if normalized in TEXT_SUBTITLE_CODECS:
        return "text subtitle"
    if normalized in BITMAP_SUBTITLE_CODECS:
        return "bitmap subtitle"
    return "unknown"


def media_info_attachment_kind(stream: dict[str, Any]) -> str:
    tags = media_info_stream_tags(stream)
    filename = str(tags.get("filename") or tags.get("FileName") or "").lower()
    mimetype = str(tags.get("mimetype") or tags.get("MIME_TYPE") or "").lower()
    if any(filename.endswith(ext) for ext in (".ttf", ".otf", ".ttc")) or "font" in mimetype:
        return "font attachment"
    if any(token in mimetype for token in ("image/", "jpeg", "png")) or "cover" in filename:
        return "cover art attachment"
    return "attachment"


def media_info_main_video_stream(payload: dict[str, Any]) -> dict[str, Any] | None:
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "video":
            return stream
    return None


def build_media_info_technical_diagnosis(
    input_path: Path,
    payload: dict[str, Any],
    stream_rows: list[dict[str, Any]],
) -> list[str]:
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    chapters = payload.get("chapters") or []
    observations: list[str] = []
    total_size = input_path.stat().st_size if input_path.exists() else format_size_bytes_from_metadata(fmt)
    numeric_rows = [row for row in stream_rows if isinstance(row.get("size_bytes"), int)]
    if numeric_rows:
        dominant = max(numeric_rows, key=lambda row: int(row["size_bytes"]))
        observations.append(
            f"The largest measured stream is stream #{dominant.get('stream_index')} "
            f"({dominant.get('type')} {dominant.get('codec')}) at {format_bytes(int(dominant['size_bytes']))}."
        )
    elif total_size:
        observations.append(f"The file size is {format_bytes(total_size)}; per-stream sizes were not fully available.")

    main_video = media_info_main_video_stream(payload)
    if main_video:
        codec = str(main_video.get("codec_name") or "unknown")
        width = main_video.get("width", "unknown")
        height = main_video.get("height", "unknown")
        main_index = main_video.get("index")
        main_row = next((row for row in stream_rows if row.get("stream_index") == main_index), None)
        bitrate = str(main_row.get("bitrate")) if main_row else describe_bitrate(stream_bitrate_kbps(main_video, fmt, None, streams))
        observations.append(
            f"The main video stream uses {media_info_video_codec_family(codec)} at {width}x{height} with bitrate {bitrate}."
        )
        if main_video.get("color_range") in {None, "", "unknown"}:
            observations.append("Color range is not declared in metadata.")

    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if audio_streams:
        audio_types = sorted({media_info_audio_coding_type(str(stream.get("codec_name") or "")) for stream in audio_streams})
        observations.append(f"Audio streams appear to be: {', '.join(audio_types)}.")

    cover_like = [
        stream for stream in streams
        if stream.get("codec_type") == "video"
        and (stream.get("disposition", {}) or {}).get("attached_pic")
    ]
    if cover_like:
        observations.append(f"{len(cover_like)} extra video stream(s) look like attached cover art.")
    observations.append("Chapters are present." if chapters else "No chapters were reported by ffprobe.")
    return observations


def append_media_info_table(lines: list[tuple[str, str]], rows: list[dict[str, Any]], indent: int = 1) -> None:
    if not rows:
        append_info_line(lines, "  " * indent + "(none)", Color.GRAY)
        return
    for row in rows:
        text = " | ".join(f"{key}: {value}" for key, value in row.items())
        append_info_line(lines, "  " * indent + text, media_info_color_for_key(str(row.get("type") or row.get("codec") or "row")))


def append_media_info_advanced_sections(
    lines: list[tuple[str, str]],
    input_path: Path,
    payload: dict[str, Any],
    analysis: dict[str, Any] | None,
    audio_volume_stats: dict[int, dict[str, str]] | None,
) -> None:
    analysis = analysis or {}
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    stream_rows = analysis.get("stream_size_rows") or media_info_stream_size_rows(input_path, payload, analysis.get("packet_sizes"))

    append_info_section(lines, "Technical Diagnosis", Color.PINK)
    for observation in build_media_info_technical_diagnosis(input_path, payload, stream_rows):
        append_info_line(lines, "  " + observation, Color.WHITE)

    append_info_section(lines, "Encoder Metadata / Encoding Settings", Color.AQUA)
    append_info_line(
        lines,
        "  Encoder metadata may be unavailable if it was stripped or never written.",
        Color.YELLOW,
    )
    append_info_line(
        lines,
        "  CRF, preset, tune, AQ, keyint, and other encoder settings cannot be reliably detected unless stored in metadata or bitstream information.",
        Color.YELLOW,
    )
    for stream in streams:
        if stream.get("codec_type") not in {"video", "audio"}:
            continue
        append_info_line(lines, "  " + media_info_stream_name(stream), Color.BOLD + Color.CYAN)
        size, estimated = stream_size_bytes(stream, fmt, analysis.get("packet_sizes"), streams)
        bitrate = stream_bitrate_kbps(stream, fmt, analysis.get("packet_sizes"), streams)
        append_info_kv(lines, "computed bitrate", describe_bitrate(bitrate), 2, Color.YELLOW)
        append_info_kv(lines, "computed stream size", f"{format_bytes(size)}{' estimated' if estimated else ''}", 2, Color.LIME)
        for key in ("codec_name", "codec_long_name", "profile", "level", "pix_fmt", "color_range", "color_space", "color_transfer", "color_primaries"):
            if stream.get(key) not in {None, ""}:
                append_info_kv(lines, key, stream.get(key), 2)
        tags = media_info_stream_tags(stream)
        encoder_tags = {key: value for key, value in tags.items() if "encod" in str(key).lower() or str(key).upper() in {"BPS", "NUMBER_OF_BYTES"}}
        if encoder_tags:
            append_nested_info(lines, encoder_tags, 2, "encoder-related tags")
        for side_index, side_data in enumerate(stream.get("side_data_list") or []):
            append_nested_info(lines, side_data, 2, f"side data {side_index}")

    append_info_section(lines, "Stream Size Analysis", Color.LIME)
    append_media_info_table(lines, stream_rows)

    append_info_section(lines, "Bits Per Pixel Per Frame", Color.ORANGE)
    bpppf_rows = analysis.get("bpppf_rows") or calculate_bpppf_rows(payload, analysis.get("packet_sizes"))
    append_media_info_table(lines, bpppf_rows)
    append_info_line(lines, "  Very low bpppf may indicate heavy compression.", Color.GRAY)
    append_info_line(lines, "  Very high bpppf may indicate large file size, near-source encode, inefficient encode, or overkill bitrate.", Color.GRAY)
    append_info_line(lines, "  This metric is only a rough technical indicator, not a final visual quality score.", Color.GRAY)

    append_info_section(lines, "Per-Second Bitrate Analysis", Color.CYAN)
    packet_summary = analysis.get("packet_bitrate")
    if packet_summary:
        for key in ("average_kbps", "minimum_kbps", "maximum_kbps", "p05_kbps", "median_kbps", "p95_kbps"):
            value = packet_summary.get(key)
            append_info_kv(lines, key, f"{value:.3f} kbps" if isinstance(value, (int, float)) else "unknown", 1)
        append_info_kv(lines, "analyzed seconds", packet_summary.get("seconds", "unknown"), 1)
        append_info_kv(lines, "CSV", packet_summary.get("csv_path", "unknown"), 1)
    else:
        append_info_line(lines, "  Skipped: " + str(analysis.get("packet_bitrate_skip") or "packet timestamps were unavailable"), Color.YELLOW)

    append_info_section(lines, "Frame Type / I-P-B Analysis", Color.MAGENTA)
    frame_summary = analysis.get("frame_analysis")
    if frame_summary:
        total = int(frame_summary.get("total_frames") or 0)
        for label, key in (("I frames", "i_frames"), ("P frames", "p_frames"), ("B frames", "b_frames")):
            count = int(frame_summary.get(key) or 0)
            percent = (count / total * 100.0) if total else 0.0
            append_info_kv(lines, label, f"{count} ({percent:.2f}%)", 1)
        append_info_kv(lines, "total analyzed frames", total, 1)
        append_info_kv(lines, "keyframes", frame_summary.get("keyframes", "unknown"), 1)
        append_info_kv(lines, "average GOP length", frame_summary.get("average_gop_frames") or "unknown", 1)
        append_info_kv(lines, "minimum GOP length", frame_summary.get("minimum_gop_frames") or "unknown", 1)
        append_info_kv(lines, "maximum GOP length", frame_summary.get("maximum_gop_frames") or "unknown", 1)
        append_info_kv(lines, "CSV", frame_summary.get("csv_path", "unknown"), 1)
        append_info_line(lines, "  Very long GOP can improve compression but may reduce seeking accuracy.", Color.GRAY)
        append_info_line(lines, "  More B-frames usually improves compression efficiency.", Color.GRAY)
        append_info_line(lines, "  Frame type distribution is technical information and does not directly prove visual quality.", Color.GRAY)
    else:
        append_info_line(lines, "  Skipped: " + str(analysis.get("frame_analysis_skip") or "frame data was unavailable"), Color.YELLOW)

    append_info_section(lines, "GOP / Keyframe Summary", Color.YELLOW)
    if frame_summary and int(frame_summary.get("keyframes") or 0) >= 2:
        for key in ("first_keyframe_time", "last_keyframe_time", "average_keyframe_interval", "minimum_keyframe_interval", "maximum_keyframe_interval", "average_gop_frames"):
            value = frame_summary.get(key)
            if isinstance(value, (int, float)):
                text = f"{value:.3f} s" if "time" in key or "interval" in key else f"{value:.2f} frames"
            else:
                text = "unknown"
            append_info_kv(lines, key, text, 1)
    else:
        append_info_line(lines, "  GOP statistics are limited because too few keyframes were available.", Color.YELLOW)

    append_info_section(lines, "Color Metadata Extended", Color.LIGHT_BLUE)
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        append_info_line(lines, "  " + media_info_stream_name(stream), Color.BOLD + Color.LIGHT_BLUE)
        for key in ("color_range", "color_space", "color_transfer", "color_primaries", "chroma_location", "pix_fmt", "bits_per_raw_sample", "field_order", "sample_aspect_ratio", "display_aspect_ratio"):
            append_info_kv(lines, key, stream.get(key, "unknown"), 2)
        if stream.get("color_range") in {None, "", "unknown"}:
            append_info_line(lines, "    Color range is not declared in metadata. This does not always mean the actual range is unknown; it means it was not signaled clearly in the file metadata.", Color.YELLOW)

    append_info_section(lines, "Audio Technical Detail", Color.BLUE)
    audio_relative = 0
    for stream in streams:
        if stream.get("codec_type") != "audio":
            continue
        append_info_line(lines, "  " + media_info_stream_name(stream), Color.BOLD + Color.BLUE)
        for key in ("codec_name", "profile", "sample_fmt", "sample_rate", "channels", "channel_layout", "bits_per_raw_sample", "bit_rate"):
            if key == "bit_rate" and stream.get(key) in {None, "", "N/A", "unknown"}:
                continue
            append_info_kv(lines, key, stream.get(key, "unknown"), 2)
        size, estimated = stream_size_bytes(stream, fmt, analysis.get("packet_sizes"), streams)
        bitrate = stream_bitrate_kbps(stream, fmt, analysis.get("packet_sizes"), streams)
        append_info_kv(lines, "computed bitrate", describe_bitrate(bitrate), 2, Color.YELLOW)
        append_info_kv(lines, "size", f"{format_bytes(size)}{' estimated' if estimated else ''}", 2)
        append_info_kv(lines, "language", display_language(media_info_stream_tags(stream).get("language")), 2)
        append_info_kv(lines, "default", media_info_disposition(stream, "default"), 2)
        append_info_kv(lines, "original", media_info_disposition(stream, "original"), 2)
        stats = audio_volume_stats or {}
        append_info_kv(lines, "mean / max volume", audio_mean_max_volume_field(stats, audio_relative), 2, Color.MEAN_VOLUME)
        audio_relative += 1

    append_info_section(lines, "Subtitle and Attachment Detail", Color.ORANGE)
    for stream in streams:
        if stream.get("codec_type") == "subtitle":
            append_info_line(lines, "  " + media_info_stream_name(stream), Color.BOLD + Color.ORANGE)
            append_info_kv(lines, "language", display_language(media_info_stream_tags(stream).get("language")), 2)
            append_info_kv(lines, "title", media_info_stream_tags(stream).get("title") or "unknown", 2)
            append_info_kv(lines, "default", media_info_disposition(stream, "default"), 2)
            append_info_kv(lines, "forced", media_info_disposition(stream, "forced"), 2)
            append_info_kv(lines, "hearing impaired", media_info_disposition(stream, "hearing_impaired"), 2)
            append_info_kv(lines, "subtitle kind", media_info_subtitle_kind(str(stream.get("codec_name") or "")), 2)
        elif stream.get("codec_type") == "attachment":
            tags = media_info_stream_tags(stream)
            append_info_line(lines, "  " + media_info_stream_name(stream), Color.BOLD + Color.PINK)
            append_info_kv(lines, "filename", tags.get("filename") or "unknown", 2)
            append_info_kv(lines, "mimetype", tags.get("mimetype") or tags.get("MIME_TYPE") or "unknown", 2)
            size, estimated = stream_size_bytes(stream, fmt, analysis.get("packet_sizes"), streams)
            append_info_kv(lines, "size", f"{format_bytes(size)}{' estimated' if estimated else ''}", 2)
            append_info_kv(lines, "attachment kind", media_info_attachment_kind(stream), 2)


def build_media_info_report_lines(
    input_path: Path,
    payload: dict[str, Any],
    text_overview: str,
    info_path: Path,
    audio_volume_stats: dict[int, dict[str, str]] | None = None,
    analysis: dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    chapters = payload.get("chapters") or []
    programs = payload.get("programs") or []

    append_info_section(lines, "Media Info Report", Color.LIGHT_BLUE)
    append_info_kv(lines, "Generated", datetime.datetime.now().isoformat(timespec="seconds"), 1, Color.WHITE)
    append_info_kv(lines, "Input path", input_path, 1, Color.WHITE)
    append_info_kv(lines, "Normalized path", _safe_resolved_path(input_path), 1, Color.CYAN)
    append_info_kv(lines, "Path exists", "yes" if input_path.exists() else "no", 1, Color.GREEN if input_path.exists() else Color.RED)
    append_info_kv(lines, "File size", format_bytes(input_path.stat().st_size if input_path.exists() else None), 1, Color.LIME)
    append_info_kv(lines, "Duration", format_duration(stream_duration_seconds({}, fmt)), 1, Color.MAGENTA)
    append_info_kv(lines, "Total bitrate", describe_total_bitrate(fmt), 1, Color.YELLOW)
    append_info_kv(lines, "Report file", info_path, 1, Color.AQUA)

    append_media_info_advanced_sections(lines, input_path, payload, analysis, audio_volume_stats)

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
    audio_relative_index = 0
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
        append_info_line(lines, "  " + info_stream_header(stream, relative_index, len(chapters)), Color.BOLD + color)
        append_info_line(lines, "  " + "-" * 46, Color.GRAY)
        if stream_type == "audio":
            stats = audio_volume_stats or {}
            append_info_kv(lines, "mean / max volume", audio_mean_max_volume_field(stats, audio_relative_index), 2, Color.MEAN_VOLUME)
            audio_relative_index += 1
        append_nested_info(lines, stream, 2)

    append_info_section(lines, "Chapters", Color.ORANGE)
    append_info_kv(lines, "Chapter count", len(chapters), 1, Color.LIGHT_BLUE)
    append_nested_info(lines, chapters, 1)

    append_info_section(lines, "Programs", Color.YELLOW)
    append_info_kv(lines, "Program count", len(programs), 1, Color.LIGHT_BLUE)
    append_nested_info(lines, programs, 1)

    append_info_section(lines, "FFprobe Text Overview", Color.LIME)
    for line in text_overview.splitlines() or ["(empty)"]:
        append_info_line(lines, "  " + line, Color.WHITE)
    return lines


def render_info_report(lines: list[tuple[str, str]], color: bool = True) -> str:
    rendered: list[str] = []
    for text, color_code in lines:
        rendered.append(paint(text, color_code) if color and text else text)
    return "\n".join(rendered)


def media_info_payload_for_report(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(payload)
    cleaned.pop("program_version", None)
    cleaned.pop("library_versions", None)
    return cleaned


def media_info_html_color(index: int) -> str:
    hue = (index * 137) % 360
    phase = index % 5
    saturation = 74 + phase * 4
    light = 58 + ((index * 3) % 18)
    chroma = saturation / 100.0
    x = chroma * (1 - abs((hue / 60.0) % 2 - 1))
    m = light / 100.0 - chroma / 2
    if hue < 60:
        r, g, b = chroma, x, 0
    elif hue < 120:
        r, g, b = x, chroma, 0
    elif hue < 180:
        r, g, b = 0, chroma, x
    elif hue < 240:
        r, g, b = 0, x, chroma
    elif hue < 300:
        r, g, b = x, 0, chroma
    else:
        r, g, b = chroma, 0, x
    return f"rgb({max(0, min(255, int((r + m) * 255)))}, {max(0, min(255, int((g + m) * 255)))}, {max(0, min(255, int((b + m) * 255)))})"


def render_info_report_html(lines: list[tuple[str, str]], input_path: Path, plain_report: str, raw_json: str, text_overview: str) -> str:
    rendered_lines: list[str] = []
    for index, (text, _color_code) in enumerate(lines):
        if text:
            rendered_lines.append(
                f'<div class="line" style="color: {media_info_html_color(index)}">{html.escape(text)}</div>'
            )
        else:
            rendered_lines.append('<div class="line blank">&nbsp;</div>')
    palette_preview = "\n".join(
        f'<span style="background:{media_info_html_color(i)}"></span>' for i in range(200)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(input_path.name)} media report</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin: 0; background: #070b10; color: #dbeafe; font-family: Consolas, 'Cascadia Mono', monospace; }}
main {{ max-width: 1500px; margin: 0 auto; padding: 24px; }}
h1 {{ margin: 0 0 6px; color: #7dd3fc; font-family: 'Segoe UI', sans-serif; }}
.path {{ color: #c4b5fd; margin-bottom: 18px; word-break: break-all; }}
.report {{ background: #0c121a; border: 1px solid #263447; border-radius: 10px; padding: 16px 18px; box-shadow: 0 16px 36px rgba(0,0,0,.35); }}
.line {{ white-space: pre-wrap; line-height: 1.42; font-size: 13px; }}
.blank {{ line-height: .7; }}
.palette {{ display: grid; grid-template-columns: repeat(50, 1fr); gap: 2px; margin: 14px 0 22px; }}
.palette span {{ height: 6px; border-radius: 2px; }}
details {{ margin-top: 16px; background: #0f1722; border: 1px solid #243244; border-radius: 8px; padding: 10px 12px; }}
summary {{ cursor: pointer; color: #fbbf24; font-weight: 700; }}
pre {{ white-space: pre-wrap; word-break: break-word; color: #d1d5db; }}
</style>
</head>
<body>
<main>
<h1>Media Info Report</h1>
<div class="path">{html.escape(str(input_path))}</div>
<div class="palette">{palette_preview}</div>
<section class="report">
{''.join(rendered_lines)}
</section>
<details>
<summary>Plain text report</summary>
<pre>{html.escape(plain_report)}</pre>
</details>
<details>
<summary>Raw ffprobe JSON</summary>
<pre>{html.escape(raw_json)}</pre>
</details>
<details>
<summary>Raw ffprobe text overview</summary>
<pre>{html.escape(text_overview.strip())}</pre>
</details>
</main>
</body>
</html>
"""


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
) -> list[Path]:
    plain_report = render_info_report(lines, color=False)
    report_payload = media_info_payload_for_report(payload)
    raw_json = json.dumps(report_payload, ensure_ascii=False, indent=2)
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
    html_path = info_path.with_suffix(".html")
    html_report = render_info_report_html(lines, input_path, plain_report, raw_json, text_overview)
    html_path.write_text(html_report, encoding="utf-8")
    raw_json_path = media_info_sidecar_path(info_path, "raw_ffprobe", ".json")
    raw_json_path.write_text(raw_json, encoding="utf-8")
    log_info(f"Media Info report written: {info_path}")
    log_info(f"Media Info HTML report written: {html_path}")
    log_info(f"Media Info raw ffprobe JSON written: {raw_json_path}")
    log_debug(f"Media Info report size: {len(content)} characters for {input_path}")
    return [html_path, raw_json_path]


def ffmpeg_filter_available(ffmpeg: str, filter_name: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        text, _ = decode_subprocess_bytes(result.stdout, "utf-8")
        return filter_name in text
    except Exception:
        log_exception(f"Could not inspect FFmpeg filters for {filter_name}")
        return False


def optional_reference_metrics(
    ffprobe: str,
    ffmpeg: str | None,
    input_path: Path,
    reference_path: Path | None,
    payload: dict[str, Any],
    info_path: Path,
    skipped: list[str],
) -> list[Path]:
    if not ffmpeg or reference_path is None:
        return []
    try:
        reference_payload = ffprobe_full_json(ffprobe, reference_path)
        main_video = media_info_main_video_stream(payload) or {}
        ref_video = media_info_main_video_stream(reference_payload) or {}
        warnings: list[str] = []
        if stream_duration_seconds({}, payload.get("format") or {}) != stream_duration_seconds({}, reference_payload.get("format") or {}):
            warnings.append("duration differs")
        if (main_video.get("width"), main_video.get("height")) != (ref_video.get("width"), ref_video.get("height")):
            warnings.append("resolution differs")
        if media_info_video_fps(main_video) != media_info_video_fps(ref_video):
            warnings.append("frame rate differs")
        if warnings:
            skipped.append("Reference metric warning: " + ", ".join(warnings) + ". Metrics only make sense when files are aligned.")
    except Exception as exc:
        skipped.append(f"Reference metric warning: could not inspect reference file metadata ({exc}).")
    generated: list[Path] = []
    metric_specs = [
        ("psnr", ["[0:v][1:v]psnr=stats_file={path}"], media_info_sidecar_path(info_path, "psnr", ".log")),
        ("ssim", ["[0:v][1:v]ssim=stats_file={path}"], media_info_sidecar_path(info_path, "ssim", ".log")),
    ]
    if ffmpeg_filter_available(ffmpeg, "libvmaf"):
        metric_specs.append(("vmaf", ["[0:v][1:v]libvmaf=log_fmt=json:log_path={path}"], media_info_sidecar_path(info_path, "vmaf", ".json")))
    else:
        skipped.append("VMAF skipped: this FFmpeg build does not report libvmaf support.")
    for label, filter_templates, output_path in metric_specs:
        lavfi = filter_templates[0].format(path=str(output_path).replace("\\", "/").replace(":", "\\:"))
        args = [
            ffmpeg,
            "-hide_banner",
            "-i", str(input_path),
            "-i", str(reference_path),
            "-lavfi", lavfi,
            "-f", "null",
            "-",
        ]
        log_info(f"Media Info {label.upper()} command: {json.dumps(args, ensure_ascii=False)}")
        print(f"Running {label.upper()} reference metric...")
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        stdout_text, _ = decode_subprocess_bytes(result.stdout, "utf-8")
        stderr_text, _ = decode_subprocess_bytes(result.stderr, "utf-8")
        metric_output = media_info_sidecar_path(info_path, f"{label}_ffmpeg_output", ".log")
        metric_output.write_text((stdout_text + "\n" + stderr_text).strip() + "\n", encoding="utf-8")
        generated.append(metric_output)
        if output_path.exists():
            generated.append(output_path)
        if result.returncode != 0:
            skipped.append(f"{label.upper()} failed; FFmpeg output saved to {metric_output}.")
    return generated


def optional_extract_screenshots(
    ffmpeg: str | None,
    input_path: Path,
    payload: dict[str, Any],
    info_path: Path,
    skipped: list[str],
) -> Path | None:
    if not ffmpeg:
        skipped.append("Screenshot extraction skipped: ffmpeg path is unavailable.")
        return None
    duration = stream_duration_seconds({}, payload.get("format") or {})
    if not duration or duration <= 0:
        skipped.append("Screenshot extraction skipped: duration is unknown.")
        return None
    screenshot_dir = info_path.with_name(f"{info_path.stem}_samples")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    for percent in (10, 25, 50, 75, 90):
        timestamp = duration * percent / 100.0
        if timestamp <= 0 or timestamp >= duration:
            continue
        output_png = screenshot_dir / f"sample_{percent:02d}pct.png"
        args = [
            ffmpeg,
            "-hide_banner",
            "-ss", f"{timestamp:.3f}",
            "-i", str(input_path),
            "-frames:v", "1",
            "-q:v", "1",
            str(output_png),
        ]
        log_info(f"Media Info screenshot command: {json.dumps(args, ensure_ascii=False)}")
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode == 0 and output_png.exists():
            created += 1
        else:
            log_warn(f"Screenshot extraction failed at {percent}% for {input_path}")
    if created <= 0:
        skipped.append("Screenshot extraction produced no files.")
    return screenshot_dir if created else None


def build_media_info_analysis(
    ffprobe: str,
    input_path: Path,
    payload: dict[str, Any],
    info_path: Path,
    deep_analysis: bool,
    sidecars: list[Path],
    skipped: list[str],
) -> dict[str, Any]:
    analysis: dict[str, Any] = {}
    packet_sizes: dict[int, int] = {}
    print("Calculating exact stream sizes...")
    log_info(f"Media Info exact stream-size packet scan started for {input_path}")
    packet_sizes = probe_packet_sizes(ffprobe, input_path)
    log_info(
        f"Media Info exact stream-size packet scan completed for {input_path}; "
        f"streams={len(packet_sizes)}"
    )
    if deep_analysis:
        log_info(f"Media Info deep analysis enabled for {input_path}")
    else:
        log_info(
            f"Media Info deep analysis disabled by user for {input_path}; "
            "exact stream sizes were still calculated, but CSV sidecars will not be generated."
        )
        skipped.append("Deep packet/frame analysis skipped by user.")
    analysis["packet_sizes"] = packet_sizes
    stream_rows = media_info_stream_size_rows(input_path, payload, packet_sizes)
    analysis["stream_size_rows"] = stream_rows
    analysis["bpppf_rows"] = calculate_bpppf_rows(payload, packet_sizes)
    if deep_analysis:
        stream_csv = media_info_sidecar_path(info_path, "stream_summary", ".csv")
        write_media_info_stream_summary_csv(stream_csv, stream_rows)
        sidecars.append(stream_csv)
        log_info(f"Media Info stream summary CSV written: {stream_csv}")
        print("Running per-second bitrate analysis...")
        packet_csv = media_info_sidecar_path(info_path, "per_second_bitrate", ".csv")
        packet_summary, packet_skip = analyze_packet_bitrate(ffprobe, input_path, packet_csv)
        if packet_summary:
            analysis["packet_bitrate"] = packet_summary
            sidecars.append(packet_csv)
            log_info(f"Media Info per-second bitrate CSV written: {packet_csv}")
        else:
            analysis["packet_bitrate_skip"] = packet_skip
            skipped.append(f"Per-second bitrate analysis skipped: {packet_skip}.")
            log_warn(f"Media Info per-second bitrate analysis skipped for {input_path}: {packet_skip}")
        print("Running frame type / GOP analysis...")
        frame_csv = media_info_sidecar_path(info_path, "frame_analysis", ".csv")
        frame_summary, frame_skip = analyze_frame_types_and_gop(ffprobe, input_path, frame_csv)
        if frame_summary:
            analysis["frame_analysis"] = frame_summary
            sidecars.append(frame_csv)
            log_info(f"Media Info frame analysis CSV written: {frame_csv}")
        else:
            analysis["frame_analysis_skip"] = frame_skip
            skipped.append(f"Frame type / GOP analysis skipped: {frame_skip}.")
            log_warn(f"Media Info frame type / GOP analysis skipped for {input_path}: {frame_skip}")
    return analysis


def create_media_info_report(
    ffprobe: str,
    input_path: Path,
    ffmpeg: str | None = None,
    options: MediaInfoOptions | None = None,
) -> MediaInfoReportResult:
    started_at = time.perf_counter()
    options = options or MediaInfoOptions()
    info_path = media_info_report_path(input_path)
    log_info(
        "Media Info report options: "
        f"input={input_path}; deep_analysis={options.deep_analysis}; "
        f"extract_screenshots={options.extract_screenshots}; "
        f"reference_path={options.reference_path if options.reference_path else 'none'}"
    )
    payload = ffprobe_full_json(ffprobe, input_path)
    text_overview = ffprobe_text_overview(ffprobe, input_path)
    sidecars: list[Path] = []
    skipped: list[str] = []
    analysis = build_media_info_analysis(ffprobe, input_path, payload, info_path, options.deep_analysis, sidecars, skipped)
    audio_streams = [stream for stream in (payload.get("streams") or []) if stream.get("codec_type") == "audio"]
    audio_volume_stats: dict[int, dict[str, str]] = {}
    if audio_streams and ffmpeg:
        volume_answers = {
            "ffmpeg": ffmpeg,
            "input_path": input_path,
            "audio_streams": audio_streams,
        }
        audio_volume_stats = get_audio_volume_stats(volume_answers)
    metric_sidecars = optional_reference_metrics(ffprobe, ffmpeg, input_path, options.reference_path, payload, info_path, skipped)
    sidecars.extend(metric_sidecars)
    screenshot_dir = optional_extract_screenshots(ffmpeg, input_path, payload, info_path, skipped) if options.extract_screenshots else None
    lines = build_media_info_report_lines(input_path, payload, text_overview, info_path, audio_volume_stats, analysis)
    append_info_section(lines, "Sidecar Files", Color.AQUA)
    display_sidecars = [*sidecars, info_path.with_suffix(".html"), media_info_sidecar_path(info_path, "raw_ffprobe", ".json")]
    for path in display_sidecars:
        append_info_line(lines, "  " + str(path), Color.WHITE)
    if screenshot_dir:
        append_info_line(lines, "  screenshots: " + str(screenshot_dir), Color.WHITE)
    if skipped:
        append_info_section(lines, "Skipped Sections", Color.YELLOW)
        for reason in skipped:
            append_info_line(lines, "  " + reason, Color.YELLOW)
    written_sidecars = write_media_info_report(input_path, lines, payload, text_overview, info_path)
    sidecars.extend(written_sidecars)
    if screenshot_dir:
        log_info(f"Media Info screenshot folder generated: {screenshot_dir}")
    if skipped:
        for reason in skipped:
            log_info(f"Media Info skipped section: {reason}")
    log_info(
        "Media Info generated outputs: "
        f"txt={info_path}; sidecars={[str(path) for path in sidecars]}; "
        f"screenshot_dir={screenshot_dir if screenshot_dir else 'none'}"
    )
    log_info(
        f"Media Info report completed for {input_path} -> {info_path} "
        f"in {time.perf_counter() - started_at:.3f}s"
    )
    return MediaInfoReportResult(lines, info_path, sidecars, screenshot_dir, skipped)


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


def media_info_next_prompt(
    answers: dict[str, Any],
    title: str,
    details: str | None = None,
    default: str | None = None,
    back: str = "back=0, quit=exit",
) -> str:
    current = int(answers.get("_question_number", 0) or 0)
    if current < 1:
        current = 1
    answers["_question_number"] = current
    prompt = question_prompt(answers, title, details, default, back)
    answers["_question_number"] = current + 1
    return prompt


def ask_media_info_options(answers: dict[str, Any], input_path: Path) -> MediaInfoOptions:
    deep_analysis = ask_yes_no(
        media_info_next_prompt(
            answers,
            "Run deep packet/frame analysis?",
            "can be slower on large files; generates bitrate/frame CSV sidecars",
            "n",
        ),
        False,
    )
    reference_path: Path | None = None
    if input_path.is_file() and ask_yes_no(
        media_info_next_prompt(
            answers,
            "Compare this file with a reference/source file for PSNR/SSIM/VMAF?",
            "quality metrics only make sense when both files are aligned and represent the same content",
            "n",
        ),
        False,
    ):
        while True:
            value = ask_required(
                media_info_next_prompt(
                    answers,
                    "Enter reference/source file path",
                    "drag and drop a file or paste a path; example: " + example_text(r"D:\Videos\source.mkv"),
                )
            )
            candidate = terminal_path(value)
            if not candidate.exists() or not candidate.is_file():
                error("Reference file not found. Enter an existing file path.")
                continue
            reference_path = candidate
            break
    screenshot_prompt = "Extract sample screenshots?" if input_path.is_file() else "Extract sample screenshots for each file?"
    extract_screenshots = ask_yes_no(
        media_info_next_prompt(
            answers,
            screenshot_prompt,
            "saves PNG samples at 10%,25%,50%,75%,90% without modifying the video",
            "n",
        ),
        False,
    )
    return MediaInfoOptions(
        deep_analysis=deep_analysis,
        extract_screenshots=extract_screenshots,
        reference_path=reference_path,
    )


def print_media_info_result(result: MediaInfoReportResult, print_lines: bool) -> None:
    if print_lines:
        print()
        print(render_info_report(result.lines, color=True))
    note(f"Media info TXT report written to: {result.info_path}")
    for path in result.sidecar_paths:
        suffix = path.suffix.lower().lstrip(".").upper()
        note(f"Media info {suffix} sidecar written to: {path}")
    if result.screenshot_dir:
        note(f"Media info screenshots written to: {result.screenshot_dir}")
    for reason in result.skipped_sections:
        note(f"Media info skipped section: {reason}")


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
        options = ask_media_info_options(answers, input_path)
        log_info(f"Media Info mode input: {input_path}")
        if input_path.is_file():
            result = create_media_info_report(answers["ffprobe"], input_path, answers.get("ffmpeg"), options)
            print_media_info_result(result, print_lines=False)
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
        sidecar_count = 0
        for index, path in enumerate(candidates, start=1):
            try:
                result = create_media_info_report(answers["ffprobe"], path, answers.get("ffmpeg"), options)
            except FFprobeError:
                skipped += 1
                log_debug(f"Media Info skipped unsupported/unreadable file: {path}")
                continue
            except Exception:
                skipped += 1
                log_exception(f"Media Info failed for file: {path}")
                continue
            written += 1
            sidecar_count += len(result.sidecar_paths) + (1 if result.screenshot_dir else 0)
            print(
                f"  {paint(str(index) + '.', Color.LIGHT_BLUE)} "
                f"{paint('wrote', Color.LIME)} {paint(path.name, Color.WHITE)} "
                f"{paint('->', Color.GRAY)} {paint(str(result.info_path), Color.AQUA)}"
            )
            for reason in result.skipped_sections:
                note(f"  skipped section for {path.name}: {reason}")

        print()
        if written:
            note(f"Media info reports written to: {default_media_reports_dir()}")
            note(f"Media info sidecar files/folders generated: {sidecar_count}")
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
    max_volume: str = ""
    mean_volume: str = ""

    @classmethod
    def from_ffprobe(
        cls,
        raw: dict[str, Any],
        fmt: dict[str, Any] | None = None,
        packet_sizes: dict[int, int] | None = None,
        volume_stats: dict[str, str] | None = None,
        sibling_streams: list[dict[str, Any]] | None = None,
    ) -> "MuxStreamInfo":
        tags = raw.get("tags") or {}
        disposition = raw.get("disposition") or {}
        size, estimated = stream_size_bytes(raw, fmt, packet_sizes, sibling_streams)
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
            bitrate_kbps=stream_bitrate_kbps(raw, fmt, packet_sizes, sibling_streams),
            size_bytes=size,
            size_estimated=estimated,
            bit_depth=video_bit_depth(raw),
            color_range=str(raw.get("color_range") or "unknown"),
            max_volume=(volume_stats or {}).get("max_volume", ""),
            mean_volume=(volume_stats or {}).get("mean_volume", ""),
        )


@dataclass
class MuxMediaFile:
    path: Path
    streams: list[MuxStreamInfo]
    format: dict[str, Any]
    chapter_count: int = 0

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
class MuxStreamMetadataEdit:
    codec_type: str
    match_indexes: list[int] = field(default_factory=list)
    match_languages: list[str] = field(default_factory=list)
    language: str = ""
    title: str = ""


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
    copy_non_video_files: bool = True
    selection_style: str = "advanced"
    metadata_edits: list[MuxStreamMetadataEdit] = field(default_factory=list)


def mux_find_video_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in MUX_CLEANUP_VIDEO_EXTS else []
    if not input_path.is_dir():
        return []
    return sorted(
        (path for path in input_path.rglob("*") if path.is_file() and path.suffix.lower() in MUX_CLEANUP_VIDEO_EXTS),
        key=lambda path: str(path.relative_to(input_path)).lower(),
    )


def mux_probe_file(ffprobe: str, path: Path, ffmpeg: str | None = None) -> MuxMediaFile | None:
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
    audio_streams = [stream for stream in raw_streams if stream.get("codec_type") == "audio"]
    audio_volume_stats: dict[int, dict[str, str]] = {}
    if audio_streams and ffmpeg:
        audio_volume_stats = get_audio_volume_stats({
            "ffmpeg": ffmpeg,
            "input_path": path,
            "audio_streams": audio_streams,
        })
    audio_relative_index = 0
    streams: list[MuxStreamInfo] = []
    for stream in raw_streams:
        stats = None
        if stream.get("codec_type") == "audio":
            stats = audio_volume_stats.get(audio_relative_index)
            audio_relative_index += 1
        streams.append(MuxStreamInfo.from_ffprobe(stream, fmt, packet_sizes, stats, raw_streams))
    media = MuxMediaFile(path=path, streams=streams, format=fmt, chapter_count=len(payload.get("chapters") or []))
    log_debug(
        f"Stream Cleanup Remux probe OK: {path}; "
        f"video={len(media.video_streams)} audio={len(media.audio_streams)} "
        f"subtitle={len(media.subtitle_streams)} attachments={len(media.attachment_streams)}"
    )
    return media


def mux_scan_files(ffprobe: str, files: list[Path], ffmpeg: str | None = None) -> list[MuxMediaFile]:
    media_files: list[MuxMediaFile] = []
    log_info(f"Stream Cleanup scan started: files={len(files)}")
    for index, path in enumerate(files, start=1):
        print(
            f"{paint('[' + str(index) + '/' + str(len(files)) + ']', Color.MUX_GOLD)} "
            f"{paint('Scanning:', Color.MUX_SCAN_HEADER)} {paint(path.name, Color.WHITE)}"
        )
        media = mux_probe_file(ffprobe, path, ffmpeg)
        if media is not None:
            media_files.append(media)
        else:
            note(f"Skipped unreadable file: {path.name}. See log file: {_log_file_text()}")
    log_info(f"Stream Cleanup scan complete: ok={len(media_files)}/{len(files)}")
    return media_files


def mux_format_stream(stream: MuxStreamInfo, fmt: dict[str, Any] | None = None, chapter_count: int = 0) -> str:
    type_color = {
        "audio": Color.BOLD + Color.MUX_AZURE,
        "subtitle": Color.BOLD + Color.MUX_VIOLET,
        "video": Color.MAGENTA,
        "attachment": Color.PINK,
    }.get(stream.codec_type, Color.WHITE)
    parts = [
        mux_pair_text("index", stream.index, Color.BOLD + Color.MUX_GOLD),
        mux_pair_text("type", stream.codec_type, type_color),
        mux_pair_text("lang", display_language(stream.language), mux_language_color(stream.language)),
        mux_pair_text("title", stream.title or "-", Color.MUX_SKY),
        mux_pair_text("codec", stream.codec_name or "-", Color.MUX_MINT),
    ]
    if stream.codec_type == "video":
        if stream.width and stream.height:
            parts.append(mux_pair_text("size", f"{stream.width}x{stream.height}", Color.LIME))
        if stream.fps:
            parts.append(mux_pair_text("fps", format(stream.fps, ".3g"), Color.MAGENTA))
        parts.append(mux_pair_text("bit depth", f"{stream.bit_depth}-bit" if stream.bit_depth else "unknown", Color.PINK))
        parts.append(mux_pair_text("Color range", display_color_range(stream.color_range), Color.COLOR_RANGE_VALUE))
        duration = stream.duration if stream.duration is not None else stream_duration_seconds({}, fmt)
        parts.append(mux_pair_text("duration", format_duration(duration), Color.MAGENTA))
        parts.append(mux_pair_text("bitrate", describe_bitrate(stream.bitrate_kbps), Color.YELLOW))
        estimate_label = " approx" if stream.size_estimated and stream.size_bytes else ""
        parts.append(mux_pair_text("video-only size", format_bytes(stream.size_bytes) + estimate_label, Color.GREEN))
        chapters_value = "yes" if chapter_count else "no"
        chapters_color = Color.CHAPTERS_YES if chapter_count else Color.CHAPTERS_NO
        parts.append(mux_pair_text("chapters", chapters_value, chapters_color))
    if stream.codec_type == "audio":
        if stream.channels is not None:
            parts.append(mux_pair_text("channels", stream.channels, Color.ORANGE))
        if stream.channel_layout:
            parts.append(mux_pair_text("layout", stream.channel_layout, Color.WHITE))
        if stream.sample_rate:
            parts.append(mux_pair_text("sample_rate", stream.sample_rate, Color.MAGENTA))
        duration = stream.duration if stream.duration is not None else stream_duration_seconds({}, fmt)
        parts.append(mux_pair_text("duration", format_duration(duration), Color.MAGENTA))
        parts.append(mux_pair_text("bitrate", describe_bitrate(stream.bitrate_kbps), Color.YELLOW))
        stats = {0: {"mean_volume": stream.mean_volume or "unknown", "max_volume": stream.max_volume or "unknown"}}
        parts.append(mux_pair_text("mean / max volume", audio_mean_max_volume_field(stats, 0), Color.MEAN_VOLUME))
        estimate_label = " approx" if stream.size_estimated and stream.size_bytes else ""
        parts.append(mux_pair_text("track size", format_bytes(stream.size_bytes) + estimate_label, Color.MUX_SILVER))
    elif stream.codec_type == "subtitle":
        duration = stream.duration if stream.duration is not None else stream_duration_seconds({}, fmt)
        parts.append(mux_pair_text("duration", format_duration(duration), Color.MAGENTA))
    default_value = "yes" if stream.disposition_default else "no"
    parts.append(mux_pair_text("default", default_value, Color.BOLD + Color.GREEN if stream.disposition_default else Color.GRAY))
    return " | ".join(parts)


def mux_display_path(input_root: Path, input_file: Path) -> Path:
    if input_root.is_file():
        return Path(input_file.name)
    try:
        return input_file.relative_to(input_root)
    except ValueError:
        return input_file


def mux_print_scan_report(media_files: list[MuxMediaFile], input_root: Path) -> None:
    mux_print_header("Stream Cleanup Scan Report", Color.MUX_SCAN_HEADER)
    for index, media in enumerate(media_files, start=1):
        print()
        if index > 1:
            print(mux_separator_line(Color.MUX_SEPARATOR))
        print(
            f"{paint('File:', Color.MUX_FILE_LINE)} "
            f"{paint(str(mux_display_path(input_root, media.path)), Color.MUX_FILE_LINE)} | "
            f"{mux_pair_text('duration', format_duration(stream_duration_seconds({}, media.format)), Color.MAGENTA)} | "
            f"{mux_pair_text('total bitrate', describe_total_bitrate(media.format), Color.YELLOW)}"
        )
        if media.video_streams:
            print(paint("  Video:", Color.BOLD + Color.MAGENTA))
            for stream in media.video_streams:
                print("    " + mux_format_stream(stream, media.format, media.chapter_count))
        else:
            print(paint("  Video: none", Color.GRAY))
        if media.audio_streams:
            print(paint("  Audio:", Color.BOLD + Color.MUX_AZURE))
            for stream in media.audio_streams:
                print("    " + mux_format_stream(stream, media.format))
        else:
            print(paint("  Audio: none", Color.GRAY))
        if media.subtitle_streams:
            print(paint("  Subtitles:", Color.BOLD + Color.MUX_VIOLET))
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


def mux_normalize_language(value: str) -> str:
    text = str(value or "").strip().lower()
    return "unknown" if text in {"", "und", "undefined"} else text


def mux_streams_for_type(media_files: list[MuxMediaFile], codec_type: str) -> list[MuxStreamInfo]:
    return [
        stream
        for media in media_files
        for stream in media.streams
        if stream.codec_type == codec_type
    ]


def mux_kept_streams_for_metadata(
    media_files: list[MuxMediaFile],
    codec_type: str,
    rules: MuxCleanupRules | None,
) -> list[MuxStreamInfo]:
    if rules is None:
        return mux_streams_for_type(media_files, codec_type)
    kept: list[MuxStreamInfo] = []
    for media in media_files:
        if codec_type == "audio":
            kept.extend(mux_selected_audio_streams(media, rules))
        elif codec_type == "subtitle":
            kept.extend(mux_selected_subtitle_streams(media, rules))
    return kept


def mux_kept_languages_for_metadata(
    media_files: list[MuxMediaFile],
    codec_type: str,
    rules: MuxCleanupRules | None,
) -> list[str]:
    return sorted({mux_normalize_language(stream.language) for stream in mux_kept_streams_for_metadata(media_files, codec_type, rules)})


def mux_kept_indexes_for_metadata(
    media_files: list[MuxMediaFile],
    codec_type: str,
    rules: MuxCleanupRules | None,
) -> list[int]:
    return sorted({stream.index for stream in mux_kept_streams_for_metadata(media_files, codec_type, rules)})


def mux_format_index_list(indexes: list[int]) -> str:
    return ", ".join(str(index) for index in indexes) if indexes else "none"


def mux_metadata_edit_match_text(edit: MuxStreamMetadataEdit) -> str:
    if edit.match_indexes:
        return "indexes=" + ",".join(str(index) for index in edit.match_indexes)
    if edit.match_languages:
        return "languages=" + ",".join(display_language(value) for value in edit.match_languages)
    return "all"


def mux_metadata_edit_change_text(edit: MuxStreamMetadataEdit) -> str:
    changes: list[str] = []
    if edit.language:
        changes.append(f"language={display_language(edit.language)}")
    if edit.title:
        changes.append(f"title={edit.title}")
    return ", ".join(changes) if changes else "no changes"


def mux_format_metadata_edit(edit: MuxStreamMetadataEdit) -> str:
    return f"{edit.codec_type} {mux_metadata_edit_match_text(edit)} -> {mux_metadata_edit_change_text(edit)}"


def mux_format_metadata_edits(edits: list[MuxStreamMetadataEdit]) -> str:
    return "; ".join(mux_format_metadata_edit(edit) for edit in edits) if edits else "none"


def mux_terminal_width() -> int:
    try:
        return max(72, shutil.get_terminal_size((100, 20)).columns)
    except Exception:
        return 100


def mux_separator_line(color_code: str = Color.MUX_SEPARATOR, char: str = "=") -> str:
    return paint(char * mux_terminal_width(), color_code)


def mux_center_text(text: str) -> str:
    return text.center(mux_terminal_width())


def mux_print_header(text: str, color_code: str = Color.MUX_HEADER, char: str = "=") -> None:
    print()
    print(paint(mux_center_text(text), color_code))
    print(mux_separator_line(color_code, char))


def mux_language_color(language: str) -> str:
    normalized = mux_normalize_language(language)
    if normalized == "unknown":
        return Color.MUX_UNKNOWN_LANGUAGE
    return MUX_LANGUAGE_COLORS[sum(ord(ch) for ch in normalized) % len(MUX_LANGUAGE_COLORS)]


def mux_pair_text(name: str, value: Any, value_color: str = Color.MUX_SETTING_VALUE) -> str:
    return f"{paint(name + ':', Color.GRAY)} {paint(str(value), value_color)}"


def mux_setting_text(name: str, value: Any, value_color: str = Color.MUX_SETTING_VALUE) -> str:
    return f"{paint(name + ':', Color.MUX_SETTING_LABEL)} {paint(str(value), value_color)}"


def mux_format_value_list(values: Any, value_color: str = Color.MUX_SETTING_VALUE) -> str:
    if isinstance(values, (list, tuple, set)):
        if not values:
            return paint("-", Color.GRAY)
        return paint(",".join(str(value) for value in values), value_color)
    if values in (None, "", []):
        return paint("-", Color.GRAY)
    return paint(str(values), value_color)


def mux_print_setting(name: str, value: Any, value_color: str = Color.MUX_SETTING_VALUE) -> None:
    print("  " + mux_setting_text(name, value, value_color))


def mux_print_unique_summary(media_files: list[MuxMediaFile]) -> None:
    mux_print_header("Unique Stream Summary", Color.MUX_SUMMARY_HEADER, "-")
    for codec_type, color_code in (("audio", Color.MUX_AUDIO), ("subtitle", Color.MUX_SUBTITLE)):
        print(paint(codec_type.capitalize() + " streams found:", Color.BOLD + color_code))
        summary: dict[tuple[str, str, str], int] = {}
        for media in media_files:
            streams = media.audio_streams if codec_type == "audio" else media.subtitle_streams
            for stream in streams:
                key = (mux_normalize_language(stream.language), stream.title or "-", stream.codec_name or "-")
                summary[key] = summary.get(key, 0) + 1
        if not summary:
            print(paint("  none", Color.GRAY))
            continue
        for (language, title, codec), count in sorted(summary.items()):
            print(
                f"  {mux_pair_text('count', count, Color.BOLD + Color.MUX_GOLD)} | "
                f"{mux_pair_text('lang', display_language(language), mux_language_color(language))} | "
                f"{mux_pair_text('title', title, Color.MUX_SKY)} | "
                f"{mux_pair_text('codec', codec, Color.MUX_MINT)}"
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


def mux_assign_prompt_number(answers: dict[str, Any]) -> int:
    number = int(answers.get("_mux_next_question_number") or answers.get("_question_number") or 1)
    answers["_question_number"] = number
    answers["_mux_next_question_number"] = number + 1
    return number


def mux_ask_choice(answers: dict[str, Any], title: str, details: str, valid: set[str], default: str) -> str:
    prompt_number = mux_assign_prompt_number(answers)
    while True:
        answers["_question_number"] = prompt_number
        value = ask_raw(question_prompt(answers, title, details, default))
        if is_back_value(value):
            raise Back()
        if not value:
            value = default
        lowered = value.lower()
        if lowered in valid:
            return lowered
        error("Enter one of: " + ", ".join(sorted(valid)))


def mux_ask_yes_no(answers: dict[str, Any], title: str, default: bool) -> bool:
    prompt_number = mux_assign_prompt_number(answers)
    answers["_question_number"] = prompt_number
    return ask_yes_no(question_prompt(answers, title, "y/n", "y" if default else "n"), default)


def mux_ask_text(answers: dict[str, Any], title: str, details: str, *, zero_is_value: bool = False) -> str:
    prompt_number = mux_assign_prompt_number(answers)
    back = "back=b, quit=exit" if zero_is_value else "back=0, quit=exit"
    while True:
        answers["_question_number"] = prompt_number
        value = ask_raw(question_prompt(answers, title, details, back=back))
        if zero_is_value:
            if value.lower().strip() in {"b", "back"}:
                raise Back()
        elif is_back_value(value):
            raise Back()
        if value:
            return value
        error("This value cannot be empty.")


def mux_ask_csv_int_required(answers: dict[str, Any], title: str, available: list[int]) -> list[int]:
    available_set = set(available)
    while True:
        raw = mux_ask_text(
            answers,
            title,
            f"available: {example_text(mux_format_index_list(available))}; use b to go back",
            zero_is_value=True,
        )
        indexes = mux_parse_csv_int(raw)
        if not indexes:
            error("Enter at least one stream index.")
            continue
        unknown = sorted(set(indexes) - available_set)
        if unknown:
            error("These indexes were not found: " + mux_format_index_list(unknown))
            continue
        return indexes


def mux_ask_language_codes_required(answers: dict[str, Any], title: str, available: list[str] | None = None) -> list[str]:
    details = "example: jpn,eng,fas"
    if available:
        details += f"; available: {example_text(','.join(available))}"
    while True:
        values = [mux_normalize_language(value) for value in mux_parse_csv_text(mux_ask_text(answers, title, details))]
        if values:
            return values
        error("Enter at least one language code.")


def mux_ask_metadata_edits(
    answers: dict[str, Any],
    media_files: list[MuxMediaFile],
    current_rules: MuxCleanupRules,
) -> list[MuxStreamMetadataEdit]:
    edits: list[MuxStreamMetadataEdit] = []
    audio_languages = mux_kept_languages_for_metadata(media_files, "audio", current_rules)
    subtitle_languages = mux_kept_languages_for_metadata(media_files, "subtitle", current_rules)
    default = "1" if "unknown" in audio_languages else "2" if "unknown" in subtitle_languages else "8"

    if not mux_ask_yes_no(answers, "Edit output stream metadata?", False):
        return edits

    while True:
        answers["_question_number"] += 1
        if edits:
            note("Current metadata edits: " + mux_format_metadata_edits(edits))
        action = mux_ask_choice(
            answers,
            "Metadata edit action",
            (
                "1=set audio language by current language; "
                "2=set subtitle language by current language; "
                "3=set audio language by exact stream indexes; "
                "4=set subtitle language by exact stream indexes; "
                "5=set audio title by exact stream indexes; "
                "6=set subtitle title by exact stream indexes; "
                "7=clear edits; 8=done"
            ),
            {"1", "2", "3", "4", "5", "6", "7", "8"},
            default if not edits else "8",
        )
        if action == "8":
            return edits
        if action == "7":
            edits = []
            note("Metadata edits cleared.")
            continue
        if action in {"1", "2"}:
            codec_type = "audio" if action == "1" else "subtitle"
            available = audio_languages if codec_type == "audio" else subtitle_languages
            if not available:
                note(f"No kept {codec_type} streams are available for metadata editing.")
                continue
            answers["_question_number"] += 1
            current_languages = mux_ask_language_codes_required(
                answers,
                f"Current {codec_type} language code(s) to edit",
                available,
            )
            answers["_question_number"] += 1
            new_language = mux_normalize_language(mux_ask_text(answers, f"New {codec_type} language code", "example: jpn"))
            edits.append(MuxStreamMetadataEdit(codec_type=codec_type, match_languages=current_languages, language=new_language))
            note("Added metadata edit: " + mux_format_metadata_edit(edits[-1]))
            continue

        codec_type = "audio" if action in {"3", "5"} else "subtitle"
        available_indexes = mux_kept_indexes_for_metadata(media_files, codec_type, current_rules)
        if not available_indexes:
            note(f"No kept {codec_type} streams are available for metadata editing.")
            continue
        answers["_question_number"] += 1
        indexes = mux_ask_csv_int_required(answers, f"{codec_type.capitalize()} stream indexes to edit", available_indexes)
        if action in {"3", "4"}:
            answers["_question_number"] += 1
            new_language = mux_normalize_language(mux_ask_text(answers, f"New {codec_type} language code", "example: jpn"))
            edits.append(MuxStreamMetadataEdit(codec_type=codec_type, match_indexes=indexes, language=new_language))
        else:
            answers["_question_number"] += 1
            new_title = mux_ask_text(answers, f"New {codec_type} title", "text title for the kept output stream")
            edits.append(MuxStreamMetadataEdit(codec_type=codec_type, match_indexes=indexes, title=new_title))
        note("Added metadata edit: " + mux_format_metadata_edit(edits[-1]))


def mux_ask_output_base(answers: dict[str, Any], input_root: Path) -> Path:
    default_text = "Enter=input parent folder"
    prompt_number = mux_assign_prompt_number(answers)
    while True:
        answers["_question_number"] = prompt_number
        value = ask_raw(question_prompt(answers, "Enter output folder path", default_text))
        if is_back_value(value):
            raise Back()
        if not value:
            return input_root.parent
        if value.lower() in {"y", "yes", "n", "no", "y/n", "yes/no", "n/y", "no/yes"}:
            error("Please enter a folder path, or press Enter to use the input parent folder.")
            continue
        path = terminal_path(value)
        if path.exists() and not path.is_dir():
            error("Output path exists but is not a folder. Enter another path.")
            continue
        if path.suffix.lower() in MUX_CLEANUP_VIDEO_EXTS:
            error("Output path must be a folder, not a media file name.")
            continue
        if path.suffix and not path.exists():
            note(f"This output folder name has an extension: {path.name}")
            if not mux_ask_yes_no(answers, "Use this as a folder path?", False):
                continue
        if not path.is_absolute():
            resolved = (Path.cwd() / path).resolve()
            note(f"Relative output folder will resolve to: {resolved}")
            if not mux_ask_yes_no(answers, "Use this relative output folder?", False):
                continue
            return resolved
        return path


def mux_configure_rules(answers: dict[str, Any], media_files: list[MuxMediaFile]) -> MuxCleanupRules:
    audio_languages = mux_unique_stream_values(media_files, "audio", "language")
    subtitle_languages = mux_unique_stream_values(media_files, "subtitle", "language")
    answers["_question_number"] = 2
    if len(audio_languages) <= 1:
        selection_style = "2"
        log_info("Stream Cleanup selection style skipped: one or zero audio languages found.")
    else:
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
            f"examples: {example_text('0,1,2')}; all; none; available: {example_text(','.join(map(str, mux_stream_indexes(media_files, 'audio'))) or 'none')}; use b to go back",
            zero_is_value=True,
        ).lower()
        if audio_value in {"all", "a", "*"}:
            audio_mode = "4"
        elif audio_value in {"none", "n", "no", "remove", "-"}:
            audio_mode = "5"
        else:
            audio_mode = "3"
            audio_indexes = mux_parse_csv_int(audio_value)

        answers["_question_number"] = 4
        subtitle_indexes_available = mux_stream_indexes(media_files, "subtitle")
        if not subtitle_indexes_available:
            subtitle_mode = "1"
            note("No subtitle streams found; selecting none.")
        else:
            subtitle_value = mux_ask_text(
                answers,
                "Subtitle stream indexes to keep",
                f"examples: {example_text('0,3,4')}; all; none; available: {example_text(','.join(map(str, subtitle_indexes_available)) or 'none')}; use b to go back",
                zero_is_value=True,
            ).lower()
            if subtitle_value in {"all", "a", "*"}:
                subtitle_mode = "5"
            elif subtitle_value in {"none", "n", "no", "remove", "-"}:
                subtitle_mode = "1"
            else:
                subtitle_mode = "4"
                subtitle_indexes = mux_parse_csv_int(subtitle_value)
    else:
        if len(audio_languages) <= 1:
            answers["_question_number"] = 3
            if audio_languages:
                audio_mode = "1"
                audio_language_values = list(audio_languages)
                note(f"Only one audio language found; keeping audio language: {', '.join(audio_language_values)}")
            else:
                audio_mode = "5"
                note("No audio streams found; selecting no audio.")
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
                audio_indexes = mux_parse_csv_int(mux_ask_text(answers, "Audio stream indexes to keep", "example: 0,2,3; use b to go back", zero_is_value=True))

        answers["_question_number"] = 5
        if not subtitle_languages:
            subtitle_mode = "1"
            note("No subtitle streams found; selecting none.")
        else:
            subtitle_mode = mux_ask_choice(
                answers,
                "Choose subtitle mode",
                f"1=remove all; 2=by language; 3=by title; 4=by exact stream indexes; 5=keep all; found languages: {example_text(','.join(subtitle_languages) or 'none')}",
                {"1", "2", "3", "4", "5"},
                "5",
            )
            if subtitle_mode == "2":
                answers["_question_number"] = 6
                subtitle_language_values = mux_parse_csv_text(mux_ask_text(answers, "Subtitle language codes to keep", "example: eng,fas"))
            elif subtitle_mode == "3":
                answers["_question_number"] = 6
                subtitle_titles = mux_parse_csv_text(mux_ask_text(answers, "Subtitle title text to keep", "example: signs,full"))
            elif subtitle_mode == "4":
                answers["_question_number"] = 6
                subtitle_indexes = mux_parse_csv_int(mux_ask_text(answers, "Subtitle stream indexes to keep", "example: 0,3,4; use b to go back", zero_is_value=True))

    answers["_question_number"] = 7
    if subtitle_mode == "1":
        keep_attachments = False
        note("Subtitle mode removes all subtitles, so font attachments will also be removed.")
    else:
        keep_attachments = mux_ask_yes_no(answers, "Keep MKV font attachments?", True)

    answers["_question_number"] = 8
    keep_metadata = mux_ask_yes_no(answers, "Keep input metadata?", True)
    metadata_context = MuxCleanupRules(
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
        keep_chapters=True,
        overwrite=False,
        copy_non_video_files=True,
        selection_style="exact" if selection_style == "1" else "advanced",
    )
    answers["_question_number"] = 9
    metadata_edits = mux_ask_metadata_edits(answers, media_files, metadata_context)
    answers["_question_number"] = int(answers.get("_question_number", 9)) + 1
    keep_chapters = mux_ask_yes_no(answers, "Keep chapters?", True)
    answers["_question_number"] = int(answers.get("_question_number", 10)) + 1
    copy_non_video_files = mux_ask_yes_no(answers, "Copy non-video files to output folder?", True)
    answers["_question_number"] = int(answers.get("_question_number", 11)) + 1
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
        copy_non_video_files=copy_non_video_files,
        selection_style="exact" if selection_style == "1" else "advanced",
        metadata_edits=metadata_edits,
    )


def mux_text_matches_any(value: str, needles: list[str]) -> bool:
    haystack = (value or "").lower()
    return any(needle in haystack for needle in needles)


def mux_selected_audio_streams(media: MuxMediaFile, rules: MuxCleanupRules) -> list[MuxStreamInfo]:
    if rules.audio_mode == "1":
        wanted = {mux_normalize_language(value) for value in rules.audio_languages}
        return [stream for stream in media.audio_streams if mux_normalize_language(stream.language) in wanted]
    if rules.audio_mode == "2":
        return [stream for stream in media.audio_streams if mux_text_matches_any(stream.title, rules.audio_titles)]
    if rules.audio_mode == "3":
        return [stream for stream in media.audio_streams if stream.index in rules.audio_indexes]
    if rules.audio_mode == "4":
        return media.audio_streams
    return []


def mux_selected_subtitle_streams(media: MuxMediaFile, rules: MuxCleanupRules) -> list[MuxStreamInfo]:
    if rules.subtitle_mode == "2":
        wanted = {mux_normalize_language(value) for value in rules.subtitle_languages}
        return [stream for stream in media.subtitle_streams if mux_normalize_language(stream.language) in wanted]
    if rules.subtitle_mode == "3":
        return [stream for stream in media.subtitle_streams if mux_text_matches_any(stream.title, rules.subtitle_titles)]
    if rules.subtitle_mode == "4":
        return [stream for stream in media.subtitle_streams if stream.index in rules.subtitle_indexes]
    if rules.subtitle_mode == "5":
        return media.subtitle_streams
    return []


def mux_metadata_edit_applies(edit: MuxStreamMetadataEdit, stream: MuxStreamInfo) -> bool:
    if edit.codec_type != stream.codec_type:
        return False
    if edit.match_indexes:
        return stream.index in edit.match_indexes
    if edit.match_languages:
        wanted = {mux_normalize_language(value) for value in edit.match_languages}
        return mux_normalize_language(stream.language) in wanted
    return True


def mux_metadata_values_for_stream(stream: MuxStreamInfo, rules: MuxCleanupRules) -> tuple[str, str]:
    language = ""
    title = ""
    for edit in rules.metadata_edits:
        if not mux_metadata_edit_applies(edit, stream):
            continue
        if edit.language:
            language = mux_normalize_language(edit.language)
        if edit.title:
            title = edit.title
    return language, title


def mux_add_stream_metadata_options(
    cmd: list[str],
    stream_spec: str,
    stream: MuxStreamInfo,
    rules: MuxCleanupRules,
) -> None:
    language, title = mux_metadata_values_for_stream(stream, rules)
    if language:
        cmd.extend([f"-metadata:{stream_spec}", f"language={language}"])
    if title:
        cmd.extend([f"-metadata:{stream_spec}", f"title={title}"])


def mux_same_stream_indexes(original: list[MuxStreamInfo], selected: list[MuxStreamInfo]) -> bool:
    return [stream.index for stream in original] == [stream.index for stream in selected]


def mux_default_disposition_needs_update(streams: list[MuxStreamInfo]) -> bool:
    if not streams:
        return False
    if streams[0].disposition_default != 1:
        return True
    return any(stream.disposition_default != 0 for stream in streams[1:])


def mux_metadata_edits_need_remux(streams: list[MuxStreamInfo], rules: MuxCleanupRules) -> bool:
    for stream in streams:
        target_language, target_title = mux_metadata_values_for_stream(stream, rules)
        if target_language and mux_normalize_language(stream.language) != mux_normalize_language(target_language):
            return True
        if target_title and (stream.title or "") != target_title:
            return True
    return False


def mux_remux_needed_reasons(
    media: MuxMediaFile,
    rules: MuxCleanupRules,
    audio_keep: list[MuxStreamInfo],
    subtitle_keep: list[MuxStreamInfo],
) -> list[str]:
    reasons: list[str] = []
    if not mux_same_stream_indexes(media.audio_streams, audio_keep):
        reasons.append("audio stream selection changes")
    if not mux_same_stream_indexes(media.subtitle_streams, subtitle_keep):
        reasons.append("subtitle stream selection changes")
    if not rules.keep_attachments and media.attachment_streams:
        reasons.append("attachments are removed")
    if not rules.keep_metadata:
        reasons.append("input metadata is removed")
    if not rules.keep_chapters:
        reasons.append("chapters are removed")
    if mux_default_disposition_needs_update(audio_keep):
        reasons.append("audio default disposition is normalized")
    if mux_default_disposition_needs_update(subtitle_keep):
        reasons.append("subtitle default disposition is normalized")
    if mux_metadata_edits_need_remux([*audio_keep, *subtitle_keep], rules):
        reasons.append("stream metadata is edited")
    return reasons


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


def mux_format_size_difference(size_bytes: int) -> str:
    sign = "+" if size_bytes > 0 else "-" if size_bytes < 0 else ""
    absolute = abs(int(size_bytes))
    kb = absolute / 1024
    mb = kb / 1024
    gb = mb / 1024
    if mb < 5:
        return f"{sign}{kb:.2f} KB"
    if gb >= 1:
        return f"{sign}{gb:.2f} GB"
    return f"{sign}{mb:.2f} MB"


def mux_path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def mux_path_total_size(path: Path, exclude_paths: list[Path] | None = None) -> int:
    excludes = list(exclude_paths or [])
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError as exc:
            log_warn(f"Could not read file size for {path}: {exc}")
            return 0
    total = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        if any(mux_path_is_under(child, excluded) for excluded in excludes):
            continue
        try:
            total += child.stat().st_size
        except OSError as exc:
            log_warn(f"Could not read file size for {child}: {exc}")
    return total


def mux_extra_file_sources(input_root: Path, output_root: Path) -> list[Path]:
    if input_root.is_file():
        return []
    sources: list[Path] = []
    for source in sorted(input_root.rglob("*"), key=lambda path: str(path).lower()):
        if not source.is_file():
            continue
        if source.suffix.lower() in MUX_CLEANUP_VIDEO_EXTS:
            continue
        if mux_path_is_under(source, output_root):
            continue
        sources.append(source)
    return sources


def mux_destination_snapshot(paths: list[Path]) -> dict[Path, tuple[int, int]]:
    snapshot: dict[Path, tuple[int, int]] = {}
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def mux_robocopy_success(returncode: int) -> bool:
    return 0 <= int(returncode) <= 7


def mux_run_robocopy(args: list[str]) -> subprocess.CompletedProcess[str]:
    log_info("robocopy command: " + command_to_text(args))
    return subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def mux_copy_video_without_remux(input_file: Path, output_file: Path) -> None:
    if shutil.which(ROBOCOPY_BIN) is None:
        raise OSError("robocopy was not found in PATH")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    before = mux_destination_snapshot([output_file])
    args = [
        ROBOCOPY_BIN,
        str(input_file.parent),
        str(output_file.parent),
        input_file.name,
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
    ]
    result = mux_run_robocopy(args)
    if result.stdout.strip():
        log_debug("robocopy stdout: " + result.stdout.strip())
    if result.stderr.strip():
        log_debug("robocopy stderr: " + result.stderr.strip())
    after = mux_destination_snapshot([output_file])
    if not mux_robocopy_success(result.returncode):
        raise OSError(f"robocopy failed with exit code {result.returncode}")
    if output_file not in after:
        raise OSError("robocopy did not create the output file")
    if before.get(output_file) == after.get(output_file):
        log_info(f"robocopy copied unchanged video but destination metadata did not change: {output_file}")


def mux_copy_extra_files(input_root: Path, output_root: Path, rules: MuxCleanupRules) -> tuple[int, int, int]:
    sources = mux_extra_file_sources(input_root, output_root)
    if not sources:
        return 0, 0, 0
    if shutil.which(ROBOCOPY_BIN) is None:
        log_warn("robocopy was not found in PATH; non-video files were not copied.")
        note("robocopy was not found in PATH; non-video files were not copied.")
        return 0, 0, len(sources)
    destinations: list[Path] = []
    for source in sources:
        try:
            destinations.append(output_root / source.relative_to(input_root))
        except ValueError:
            continue
    before = mux_destination_snapshot(destinations)
    args = [
        ROBOCOPY_BIN,
        str(input_root),
        str(output_root),
        "/E",
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        "/XF",
    ]
    args.extend(f"*{suffix}" for suffix in sorted(MUX_CLEANUP_VIDEO_EXTS))
    if mux_path_is_under(output_root, input_root):
        args.extend(["/XD", str(output_root)])
    if not rules.overwrite:
        args.extend(["/XC", "/XN", "/XO"])
    result = mux_run_robocopy(args)
    if result.stdout.strip():
        log_debug("robocopy stdout: " + result.stdout.strip())
    if result.stderr.strip():
        log_debug("robocopy stderr: " + result.stderr.strip())
    after = mux_destination_snapshot(destinations)
    copied = skipped = failed = 0
    for destination in destinations:
        before_stat = before.get(destination)
        after_stat = after.get(destination)
        if after_stat is None:
            failed += 1
        elif before_stat is None or after_stat != before_stat:
            copied += 1
        else:
            skipped += 1
    if not mux_robocopy_success(result.returncode):
        failed = max(failed, 1)
        log_warn(f"robocopy failed with exit code {result.returncode}")
    log_info(
        f"Stream Cleanup non-video copy: copied={copied}; skipped={skipped}; "
        f"failed={failed}; returncode={result.returncode}"
    )
    return copied, skipped, failed


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
    for index, _stream in enumerate(audio_keep):
        cmd.extend([f"-disposition:a:{index}", "+default" if index == 0 else "-default"])
    for index, _stream in enumerate(subtitle_keep):
        cmd.extend([f"-disposition:s:{index}", "+default" if index == 0 else "-default"])
    for index, stream in enumerate(audio_keep):
        mux_add_stream_metadata_options(cmd, f"s:a:{index}", stream, rules)
    for index, stream in enumerate(subtitle_keep):
        mux_add_stream_metadata_options(cmd, f"s:s:{index}", stream, rules)
    cmd.append(str(output_file))
    return cmd, audio_keep, subtitle_keep


def mux_print_confirm(input_root: Path, output_base: Path, output_root: Path, rules: MuxCleanupRules) -> None:
    mux_print_header("Confirm Stream Cleanup Remux", Color.MUX_CONFIRM_HEADER, "-")
    mux_print_setting("Input", input_root, Color.MUX_INPUT_PATH)
    mux_print_setting("Output base", output_base, Color.MUX_OUTPUT_BASE)
    mux_print_setting("Output root", output_root, Color.MUX_OUTPUT_ROOT)
    mux_print_setting("Audio mode", rules.audio_mode, Color.MUX_MODE)
    mux_print_setting("Audio languages", mux_format_value_list(rules.audio_languages, Color.MUX_AUDIO))
    mux_print_setting("Audio titles", mux_format_value_list(rules.audio_titles, Color.MUX_AUDIO))
    mux_print_setting("Audio indexes", mux_format_value_list(rules.audio_indexes, Color.MUX_AUDIO))
    mux_print_setting("Subtitle mode", rules.subtitle_mode, Color.MUX_MODE)
    mux_print_setting("Subtitle languages", mux_format_value_list(rules.subtitle_languages, Color.MUX_SUBTITLE))
    mux_print_setting("Subtitle titles", mux_format_value_list(rules.subtitle_titles, Color.MUX_SUBTITLE))
    mux_print_setting("Subtitle indexes", mux_format_value_list(rules.subtitle_indexes, Color.MUX_SUBTITLE))
    mux_print_setting("Metadata edits", mux_format_metadata_edits(rules.metadata_edits), Color.CYAN)
    mux_print_setting("Keep attachments", rules.keep_attachments, Color.MUX_TRUE if rules.keep_attachments else Color.MUX_FALSE)
    mux_print_setting("Keep metadata", rules.keep_metadata, Color.MUX_TRUE if rules.keep_metadata else Color.MUX_FALSE)
    mux_print_setting("Keep chapters", rules.keep_chapters, Color.MUX_TRUE if rules.keep_chapters else Color.MUX_FALSE)
    mux_print_setting("Copy non-video files", rules.copy_non_video_files, Color.MUX_TRUE if rules.copy_non_video_files else Color.MUX_FALSE)
    mux_print_setting("Overwrite", rules.overwrite, Color.MUX_FALSE if rules.overwrite else Color.MUX_TRUE)
    log_info(
        "Stream Cleanup confirmed rules: "
        f"input={input_root}; output_base={output_base}; output_root={output_root}; "
        f"audio_mode={rules.audio_mode}; audio_languages={rules.audio_languages}; "
        f"audio_titles={rules.audio_titles}; audio_indexes={rules.audio_indexes}; "
        f"subtitle_mode={rules.subtitle_mode}; subtitle_languages={rules.subtitle_languages}; "
        f"subtitle_titles={rules.subtitle_titles}; subtitle_indexes={rules.subtitle_indexes}; "
        f"keep_attachments={rules.keep_attachments}; keep_metadata={rules.keep_metadata}; "
        f"keep_chapters={rules.keep_chapters}; copy_non_video_files={rules.copy_non_video_files}; "
        f"overwrite={rules.overwrite}; metadata_edits={mux_format_metadata_edits(rules.metadata_edits)}"
    )


def mux_process_files(
    ffmpeg: str,
    media_files: list[MuxMediaFile],
    input_root: Path,
    output_root: Path,
    rules: MuxCleanupRules,
) -> tuple[int, float]:
    mux_print_header("Processing Stream Cleanup Remux", Color.MUX_PROCESS_HEADER)
    started_at = time.perf_counter()
    total = len(media_files)
    succeeded = 0
    skipped = 0
    no_audio = 0
    failed = 0
    remuxed = 0
    copied_unchanged = 0
    output_files_for_size: list[Path] = []
    output_root.mkdir(parents=True, exist_ok=True)
    log_info(f"Stream Cleanup Remux processing started: input={input_root}; output={output_root}; files={total}; rules={rules}")
    for index, media in enumerate(media_files, start=1):
        output_file = mux_make_output_path(input_root, output_root, media.path, rules)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        rel = mux_display_path(input_root, media.path)
        audio_keep = mux_selected_audio_streams(media, rules)
        subtitle_keep = mux_selected_subtitle_streams(media, rules)
        if rules.audio_mode != "5" and not audio_keep and media.audio_streams:
            no_audio += 1
            note(f"[{index}/{total}] Skip no matching audio selected: {rel}")
            log_warn(f"Stream Cleanup no matching audio selected: {media.path}")
            continue
        if output_file.exists() and not rules.overwrite:
            skipped += 1
            note(f"[{index}/{total}] Skip existing output: {rel}")
            log_warn(f"Stream Cleanup Remux skip existing output: {output_file}")
            continue
        remux_reasons = mux_remux_needed_reasons(media, rules, audio_keep, subtitle_keep)
        if not remux_reasons:
            print()
            print(
                f"{paint('[' + str(index) + '/' + str(total) + ']', Color.MUX_GOLD)} "
                f"{paint('Copying unchanged:', Color.MUX_PROCESS_HEADER)} {paint(str(rel), Color.WHITE)}"
            )
            print(paint("  no remux needed", Color.GRAY))
            log_info(f"Stream Cleanup copy unchanged: input={media.path}; output={output_file}")
            try:
                mux_copy_video_without_remux(media.path, output_file)
            except OSError as exc:
                failed += 1
                log_exception(f"Could not copy unchanged video: {media.path} -> {output_file}")
                error(f"FAILED to copy unchanged file: {exc}")
                continue
            succeeded += 1
            copied_unchanged += 1
            output_files_for_size.append(output_file)
            note(f"OK: {output_file}")
            continue
        cmd, audio_keep, subtitle_keep = mux_build_ffmpeg_command(ffmpeg, media.path, output_file, media, rules)
        log_info(
            f"Stream Cleanup Remux file {index}/{total}: input={media.path}; output={output_file}; "
            f"audio_keep={[s.index for s in audio_keep]}; subtitle_keep={[s.index for s in subtitle_keep]}; "
            f"attachments={rules.keep_attachments}; remux_reasons={remux_reasons}"
        )
        if not media.video_streams:
            note(f"[{index}/{total}] Warning: no video stream found: {rel}")
        print()
        print(
            f"{paint('[' + str(index) + '/' + str(total) + ']', Color.MUX_GOLD)} "
            f"{paint('Remuxing:', Color.MUX_PROCESS_HEADER)} {paint(str(rel), Color.WHITE)}"
        )
        print(
            "  "
            + field_text("audio kept", len(audio_keep), Color.MUX_AUDIO)
            + " | "
            + field_text("subtitles kept", len(subtitle_keep), Color.MUX_SUBTITLE)
            + " | "
            + field_text("attachments", "yes" if rules.keep_attachments else "no", Color.PINK)
        )
        duration = stream_duration_seconds({}, media.format) or None
        rc, _elapsed = run_ffmpeg_with_progress(cmd, total_duration=duration, label="Stream Cleanup Remux")
        if rc == 0:
            succeeded += 1
            remuxed += 1
            output_files_for_size.append(output_file)
            note(f"OK: {output_file}")
        else:
            failed += 1
            error(f"FAILED: {media.path}. See log file: {_log_file_text()}")
    if rules.copy_non_video_files:
        extra_copied, extra_skipped, extra_failed = mux_copy_extra_files(input_root, output_root, rules)
    else:
        extra_copied = extra_skipped = extra_failed = 0
        log_info("Stream Cleanup non-video file copy skipped by user setting.")
    elapsed = time.perf_counter() - started_at
    if input_root.is_dir():
        original_total_size = mux_path_total_size(input_root, exclude_paths=[output_root])
        output_total_size = mux_path_total_size(output_root)
    else:
        original_total_size = mux_path_total_size(input_root)
        output_total_size = sum(mux_path_total_size(path) for path in output_files_for_size)
    size_delta = output_total_size - original_total_size
    size_delta_text = mux_format_size_difference(size_delta)
    mux_print_header("Stream Cleanup Remux Done", Color.MUX_DONE_HEADER)
    mux_print_setting("Total", total, Color.WHITE)
    mux_print_setting("OK", succeeded, Color.MUX_TRUE)
    mux_print_setting("Remuxed", remuxed, Color.MUX_AZURE)
    mux_print_setting("Copied unchanged", copied_unchanged, Color.LIME)
    mux_print_setting("Skipped", skipped, Color.YELLOW)
    mux_print_setting("No audio match", no_audio, Color.ORANGE)
    mux_print_setting("Failed", failed, Color.RED if failed else Color.MUX_TRUE)
    mux_print_setting("Extra files copied", extra_copied, Color.MUX_AQUA)
    mux_print_setting("Extra files skipped", extra_skipped, Color.YELLOW)
    mux_print_setting("Extra files failed", extra_failed, Color.RED if extra_failed else Color.MUX_TRUE)
    mux_print_setting("Output", output_root, Color.MUX_OUTPUT_ROOT)
    mux_print_setting("Size difference", size_delta_text, Color.MUX_SIZE_DIFF)
    mux_print_setting("Total time elapsed", format_elapsed(elapsed), Color.MUX_ELAPSED)
    log_info(
        f"Stream Cleanup Remux done: total={total}; ok={succeeded}; remuxed={remuxed}; "
        f"copied_unchanged={copied_unchanged}; skipped={skipped}; no_audio_match={no_audio}; "
        f"failed={failed}; extra_copied={extra_copied}; extra_skipped={extra_skipped}; "
        f"extra_failed={extra_failed}; original_size_bytes={original_total_size}; "
        f"output_size_bytes={output_total_size}; size_difference={size_delta_text}; "
        f"size_delta_bytes={size_delta}; elapsed={format_elapsed(elapsed)}; output={output_root}"
    )
    return (1 if failed else 0), elapsed


def mux_verify_output(ffprobe: str, root: Path) -> None:
    files = mux_find_video_files(root)
    mux_print_header("Verify Stream Cleanup Output", Color.MUX_VERIFY_HEADER, "-")
    if not files:
        note("No supported video files found.")
        return
    log_info(f"Stream Cleanup verify output: root={root}; files={len(files)}")
    for path in files:
        media = mux_probe_file(ffprobe, path)
        if media is None:
            continue
        audio_langs = ",".join(display_language(stream.language) for stream in media.audio_streams) or "-"
        subtitle_langs = ",".join(display_language(stream.language) for stream in media.subtitle_streams) or "-"
        print(
            f"{paint(str(mux_display_path(root, path)), Color.MUX_FILE_LINE)} | "
            f"{field_text('video', len(media.video_streams), Color.MAGENTA)} | "
            f"{field_text('audio', len(media.audio_streams), Color.MUX_AUDIO)} "
            f"{paint('[' + audio_langs + ']', Color.MUX_AUDIO)} | "
            f"{field_text('subs', len(media.subtitle_streams), Color.MUX_SUBTITLE)} "
            f"{paint('[' + subtitle_langs + ']', Color.MUX_SUBTITLE)} | "
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
    muxcls_path = Path(__file__).resolve().parent / "assets" / FFMWIZ_RUNTIME_DIR_NAME / "MuxCls.py"
    if not muxcls_path.exists():
        error(f"Local Stream Cleanup runtime file was not found: {muxcls_path}")
        return None
    started_at = time.perf_counter()
    old_argv = list(sys.argv)
    old_embedded = os.environ.get("FFMWIZ_EMBEDDED_MUXCLS")
    old_log_file = os.environ.get("FFMWIZ_LOG_FILE")
    try:
        import importlib.util

        os.environ["FFMWIZ_EMBEDDED_MUXCLS"] = "1"
        if log_path() is not None:
            os.environ["FFMWIZ_LOG_FILE"] = str(log_path())
        sys.argv = [str(muxcls_path)]
        spec = importlib.util.spec_from_file_location("ffmwiz_local_muxcls", muxcls_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load local MuxCls module: {muxcls_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["ffmwiz_local_muxcls"] = module
        spec.loader.exec_module(module)
        log_info(f"Starting embedded Stream Cleanup Remux from local copy: {muxcls_path}")
        module.main_menu()
        return None
    except getattr(sys.modules.get("ffmwiz_local_muxcls"), "MenuExit", ExitWizard):
        raise ExitWizard()
    except getattr(sys.modules.get("ffmwiz_local_muxcls"), "MenuBack", Back):
        note("Returning to main menu.")
        return None
    except SystemExit as exc:
        code = int(exc.code or 0) if isinstance(exc.code, int) else 1
        if code == 0:
            return None
        return code, time.perf_counter() - started_at
    except Back:
        note("Returning to main menu.")
        return None
    finally:
        sys.argv = old_argv
        if old_embedded is None:
            os.environ.pop("FFMWIZ_EMBEDDED_MUXCLS", None)
        else:
            os.environ["FFMWIZ_EMBEDDED_MUXCLS"] = old_embedded
        if old_log_file is None:
            os.environ.pop("FFMWIZ_LOG_FILE", None)
        else:
            os.environ["FFMWIZ_LOG_FILE"] = old_log_file


def _run_mux_cleanup_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    input_root = ask_mux_cleanup_input_path(answers)
    log_info(f"Stream Cleanup Remux input: {input_root}")
    files = mux_find_video_files(input_root)
    if not files:
        error("No supported video files were found.")
        return None
    note(f"Found {len(files)} supported video file(s).")
    media_files = mux_scan_files(answers["ffprobe"], files, answers.get("ffmpeg"))
    if not media_files:
        error("No files could be scanned successfully.")
        return None
    mux_print_scan_report(media_files, input_root)
    mux_print_unique_summary(media_files)

    while True:
        try:
            answers["_mux_next_question_number"] = 2
            rules = mux_configure_rules(answers, media_files)
            output_base = mux_ask_output_base(answers, input_root)
            output_root = mux_resolve_output_root(input_root, output_base, rules)
            mux_print_confirm(input_root, output_base, output_root, rules)
            if not mux_ask_yes_no(answers, "Start Stream Cleanup Remux now?", True):
                note("Stream Cleanup Remux was not started.")
                return None
            break
        except Back:
            note("Back. Returning to stream selection.")
            continue
    result = mux_process_files(answers["ffmpeg"], media_files, input_root, output_root, rules)
    try:
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
    "attachment_streams",
    "packet_sizes",
    "audio_volume_stats",
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
        (path for path in files if is_folder_media_candidate(path) and not looks_like_generated_output_file(path)),
        key=lambda path: str(path.relative_to(folder_path)).lower(),
    )
    skipped_non_media = sum(1 for path in files if not is_folder_media_candidate(path))
    skipped_generated = sum(1 for path in files if is_folder_media_candidate(path) and looks_like_generated_output_file(path))
    log_info(
        f"Folder Encode scan: folder={folder_path}; media candidates={len(candidates)}; "
        f"non-media files ignored={skipped_non_media}; generated outputs ignored={skipped_generated}"
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
            chapters_value, chapters_color = chapter_presence(detail_answers)
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
                f"{field_text('total bitrate', describe_total_bitrate(fmt), Color.AQUA)} | "
                f"{field_text('chapters', chapters_value, chapters_color)}"
            )

        for audio_idx, stream in enumerate(detail_answers.get("audio_streams", [])):
            volume_stats = get_audio_volume_stats(detail_answers)
            item["answers"]["audio_volume_stats"] = volume_stats
            rate = stream_bitrate_kbps(stream, fmt, packet_sizes)
            size, estimated = stream_size_bytes(stream, fmt, packet_sizes)
            estimate_label = " approx" if estimated and size else ""
            print(
                f"     {paint('audio ' + str(audio_idx), Color.BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, audio_idx), Color.MEAN_VOLUME)} | "
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


def nvenc_encoder_available(video_encoders: list[str] | tuple[str, ...] | set[str] | None) -> bool:
    encoders = {str(name).lower() for name in (video_encoders or [])}
    return any(name.endswith("_nvenc") for name in encoders)


def detect_nvidia_gpu_available(ffmpeg: str, video_encoders: list[str] | None = None) -> bool:
    if video_encoders is not None and not nvenc_encoder_available(video_encoders):
        log_info("GPU auto-detect: FFmpeg does not report NVENC encoders.")
        return False
    encoder = "h264_nvenc"
    encoders = {str(name).lower() for name in (video_encoders or [])}
    if encoders and encoder not in encoders:
        encoder = "hevc_nvenc" if "hevc_nvenc" in encoders else next((name for name in encoders if name.endswith("_nvenc")), encoder)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        # 256x256: NVENC rejects tiny frames ("Frame Dimension less than the
        # minimum supported value"), so a 16x16 probe falsely reported "no GPU"
        # even on cards that fully support NVENC. 256x256 clears the minimum for
        # h264/hevc/av1 NVENC while staying a trivially fast probe.
        "-i",
        "nullsrc=s=256x256:d=0.1",
        "-frames:v",
        "1",
        "-c:v",
        encoder,
        "-f",
        "null",
        os.devnull,
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=6)
    except Exception as exc:
        log_info(f"GPU auto-detect: NVENC probe failed to run: {exc}")
        return False
    available = result.returncode == 0
    log_info(f"GPU auto-detect: NVENC probe encoder={encoder}; available={available}; returncode={result.returncode}")
    return available


def detect_gpu_model_name(ffmpeg: str) -> str | None:
    """Best-effort human-readable NVIDIA GPU model for the startup banner.

    Tries nvidia-smi first (exact marketing name, e.g. "NVIDIA GeForce RTX 4070
    Ti"); if that is unavailable, parses the GPU name FFmpeg prints while
    initialising NVENC. Returns None when no name can be determined.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=5,
        )
        if result.returncode == 0:
            for raw in result.stdout.decode("utf-8", "replace").splitlines():
                name = raw.strip()
                if name:
                    return name
    except Exception as exc:
        log_info(f"GPU model: nvidia-smi query failed: {exc}")
    # Fallback: read the device name from FFmpeg's verbose NVENC init log.
    try:
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "verbose", "-f", "lavfi",
            "-i", "nullsrc=s=256x256:d=0.1", "-frames:v", "1",
            "-c:v", "h264_nvenc", "-f", "null", os.devnull,
        ]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False, timeout=8)
        match = re.search(r"GPU #\d+\s*-\s*<\s*([^>]+?)\s*>", result.stderr.decode("utf-8", "replace"))
        if match:
            return match.group(1).strip()
    except Exception as exc:
        log_info(f"GPU model: FFmpeg NVENC name probe failed: {exc}")
    return None


def gpu_available_for_answers(answers: dict[str, Any]) -> bool:
    if "gpu_available" in answers:
        return bool(answers.get("gpu_available"))
    available = detect_nvidia_gpu_available(str(answers.get("ffmpeg") or "ffmpeg"), list(answers.get("video_encoders") or []))
    answers["gpu_available"] = available
    return available


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

# Canonical PowerShell launcher for FFmWiz.
# - Resolves project root and {script_name} relative to this script's location, so
#   the launcher works regardless of the caller's current working directory.
# - Prefers the Windows Python launcher (py -3), then python, then python3.
# - Forwards every argument unchanged to {script_name}.
# - Returns the same exit code as the Python process.
# - Does not require admin rights and does not hard-code user-specific paths.

$ErrorActionPreference = 'Stop'

# Resolve project root relative to this launcher.
$scriptDir = if ($PSScriptRoot) {{
    $PSScriptRoot
}} elseif ($PSCommandPath) {{
    Split-Path -Parent $PSCommandPath
}} else {{
    $null
}}

if (-not $scriptDir -or -not (Test-Path -LiteralPath $scriptDir)) {{
    Write-Host "run.ps1 could not determine its own directory. Re-run it as a file (not piped into PowerShell)." -ForegroundColor Red
    exit 1
}}

$projectRoot = (Resolve-Path -LiteralPath $scriptDir).Path
$scriptPath = Join-Path -Path $projectRoot -ChildPath '{script_name}'

if (-not (Test-Path -LiteralPath $scriptPath)) {{
    Write-Host "{script_name} was not found next to run.ps1. Expected at: $scriptPath" -ForegroundColor Red
    Write-Host "Make sure run.ps1 sits in the FFmWiz repository root alongside {script_name}." -ForegroundColor Red
    exit 1
}}

# Forward every CLI argument unchanged. ValueFromRemainingArguments preserves
# user-supplied flags including paths with spaces.
$forwarded = @()
if ($ScriptArgs) {{ $forwarded = @($ScriptArgs) }}

function Test-FFmWizPython {{
    param(
        [Parameter(Mandatory = $true)]
        [string]$Exe,
        [string[]]$Args = @()
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

# Probe interpreters in priority order: py -3, python, python3.
$pythonExe = $null
$pythonArgs = @()

if (Get-Command py -ErrorAction SilentlyContinue) {{
    if (Test-FFmWizPython -Exe 'py' -Args @('-3')) {{
        $pythonExe = 'py'
        $pythonArgs = @('-3')
    }}
}}
if (-not $pythonExe -and (Get-Command python -ErrorAction SilentlyContinue)) {{
    if (Test-FFmWizPython -Exe 'python') {{
        $pythonExe = 'python'
        $pythonArgs = @()
    }}
}}
if (-not $pythonExe -and (Get-Command python3 -ErrorAction SilentlyContinue)) {{
    if (Test-FFmWizPython -Exe 'python3') {{
        $pythonExe = 'python3'
        $pythonArgs = @()
    }}
}}

if (-not $pythonExe) {{
    Write-Host "Python was not found in PATH. Install Python 3.10+ and reopen the terminal." -ForegroundColor Red
    Write-Host "Tried: py -3, python, python3." -ForegroundColor Red
    exit 9009
}}

# Run {script_name} and propagate its exit code unchanged.
& $pythonExe @pythonArgs $scriptPath @forwarded
$pythonExitCode = if ($null -ne $LASTEXITCODE) {{ $LASTEXITCODE }} else {{ 0 }}
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


BACK_INPUT_TOKENS = {"0", "۰", "٠"}


def is_back_value(value: str, *, allow_text: bool = False) -> bool:
    lowered = str(value).strip().lower()
    if lowered in BACK_INPUT_TOKENS:
        return True
    return allow_text and lowered in {"b", "back"}


def ask_raw(prompt: str) -> str:
    value = strip_quotes(input(prompt).strip())
    if value.lower() == "exit":
        log_info(f"User input: prompt={_strip_ansi(prompt).strip().replace(chr(10), ' ')}; action=quit")
        raise ExitWizard()
    log_info(
        "User input: "
        f"prompt={_strip_ansi(prompt).strip().replace(chr(10), ' ')}; "
        f"value={value!r}; default_used={'yes' if value == '' else 'no'}; "
        f"action={'back' if is_back_value(value, allow_text=True) else 'answer'}"
    )
    return value


def ask_required(prompt: str, allow_n: bool = False) -> str:
    while True:
        value = ask_raw(prompt)
        if is_back_value(value):
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
        if is_back_value(value):
            raise Back()
        if not value:
            log_info(f"User choice: yes_no={default}; default_used=yes")
            return default
        lowered = value.lower()
        if lowered in {"y", "yes"}:
            log_info("User choice: yes_no=True; default_used=no")
            return True
        if lowered in {"n", "no"}:
            log_info("User choice: yes_no=False; default_used=no")
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
    """Archived standalone Crop Editor GUI.

    Normal CLI prompts no longer call this helper. Use the Unified Video
    Editor for active graphical crop workflows.
    """
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
        "Falling back to the archived legacy Tk crop preview. To enable the archived Qt helper later, "
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
    """Archived standalone Cut Editor GUI.

    Normal CLI prompts no longer call this helper. Use the Unified Video
    Editor for active graphical cut workflows; Mode 3 is manual-only.
    """
    request = {
        "mode": "cut",
        "input_path": str(answers["input_path"]),
        "fps": float(fps),
        "duration": float(duration),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "ffprobe": answers.get("ffprobe") or shutil.which("ffprobe") or "ffprobe",
        "chapters": (answers.get("probe") or {}).get("chapters") or [],
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
        "Falling back to the archived legacy Tk cut editor. To enable the archived Qt helper later, "
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
    join_segments: list[dict[str, Any]] = []
    if answers.get("join_input_items"):
        first_segment = {
            "path": str(answers["input_path"]),
            "name": Path(answers["input_path"]).name,
            "duration": float(duration),
            "chapters": (answers.get("probe") or {}).get("chapters") or [],
        }
        join_segments.append(first_segment)
        for item in answers.get("join_input_items") or []:
            join_segments.append(
                {
                    "path": str(item.get("path")),
                    "name": Path(item.get("path")).name,
                    "duration": float(item.get("duration") or 0.0),
                    "chapters": (item.get("probe") or {}).get("chapters") or [],
                }
            )
        if join_segments:
            duration = sum(max(0.0, float(segment.get("duration") or 0.0)) for segment in join_segments)
    try:
        source_w, source_h = first_video_size(answers)
    except Exception:
        source_w, source_h = 1920, 1080
    chapters = []
    if join_segments:
        offset = 0.0
        for segment_idx, segment in enumerate(join_segments, start=1):
            for chapter in segment.get("chapters") or []:
                copied = dict(chapter)
                try:
                    start_time = float(copied.get("start_time", copied.get("start", 0)))
                    end_time = float(copied.get("end_time", copied.get("end", start_time)))
                    copied["start_time"] = f"{start_time + offset:.6f}"
                    copied["end_time"] = f"{end_time + offset:.6f}"
                except Exception:
                    pass
                tags = dict(copied.get("tags") or {})
                if tags.get("title"):
                    tags["title"] = f"{tags['title']} (Video {segment_idx})"
                copied["tags"] = tags
                chapters.append(copied)
            offset += max(0.0, float(segment.get("duration") or 0.0))
    else:
        chapters = (answers.get("probe") or {}).get("chapters") or []
    request = {
        "mode": "video_unified",
        "input_path": str(answers["input_path"]),
        "duration": float(duration),
        "fps": float(get_video_fps(answers)),
        "source_w": int(source_w),
        "source_h": int(source_h),
        "has_audio": bool(answers.get("audio_streams")),
        "audio_count": len(answers.get("audio_streams") or []),
        "chapters": chapters,
        "join_segments": join_segments,
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
        "start_maximized": True,
    }
    if join_segments:
        log_info(
            "Opening Unified Video Editor with joined inputs: "
            + ", ".join(f"{idx + 1}:{Path(segment.get('path') or '').name}" for idx, segment in enumerate(join_segments))
        )
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
            separators = normalize_separator_points(reply.get("separator_points") or [], duration)
            return {
                "margins": (top, left, right, bottom),
                "keep_ranges": normalize_cut_ranges(keep_ranges, duration),
                "separator_points": separators,
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


def normalize_audio_codec(codec: Any, default: str | None = None) -> str:
    text = str(codec or default or DEFAULT_AUDIO_CODEC).strip()
    lowered = text.lower()
    if lowered == "n":
        return "copy"
    return AUDIO_CODEC_ALIASES.get(lowered, lowered)


def audio_codec_uses_bitrate(codec: str) -> bool:
    lowered = normalize_audio_codec(codec)
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
    attachment_streams = [stream for stream in streams if stream.get("codec_type") == "attachment"]
    data_streams = [stream for stream in streams if stream.get("codec_type") == "data"]
    attachment_streams = [stream for stream in streams if stream.get("codec_type") == "attachment"]
    data_streams = [stream for stream in streams if stream.get("codec_type") == "data"]
    if not video_streams and not audio_streams:
        raise ValueError("This file has no detectable video or audio streams.")

    answers["input_path"] = input_path
    answers["probe"] = probe
    answers["format"] = probe.get("format", {})
    answers["video_streams"] = video_streams
    answers["audio_streams"] = audio_streams
    answers["subtitle_streams"] = subtitle_streams
    answers["attachment_streams"] = attachment_streams
    answers["data_streams"] = data_streams
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


def looks_like_generated_output_file(path: Path) -> bool:
    stem = path.stem.lower()
    return any(stem.endswith(suffix.lower()) for suffix in GENERATED_OUTPUT_SUFFIXES)


def resolve_output_collision(output_path: Path, input_path: Path, collision_suffix: str) -> Path:
    """Avoid writing over the source file when output name and extension match."""
    if not paths_same(output_path, input_path):
        return output_path
    safe_stem = sanitize_output_stem(output_path.stem)
    candidate = output_path.with_name(f"{safe_stem}{collision_suffix}{output_path.suffix}")
    return unique_numbered_path(candidate)


def resolve_output_collision_against_inputs(output_path: Path, input_paths: list[Path], collision_suffix: str) -> Path:
    """Avoid writing the output over any source input, including joined inputs."""
    resolved = output_path
    for input_path in input_paths:
        if paths_same(resolved, input_path):
            safe_stem = sanitize_output_stem(resolved.stem)
            resolved = unique_numbered_path(resolved.with_name(f"{safe_stem}{collision_suffix}{resolved.suffix}"))
            log_info(f"Output path matched an input path; using safe output path instead: {resolved}")
            break
    return resolved


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
    if is_back_value(value):
        raise Back()
    apply_output_location_value(answers, value)
    join_items = list(answers.get("join_input_items") or [])
    if join_items:
        original_title = answers.get("_source_info_title")
        answers["_source_info_title"] = f"Source file info (1/{len(join_items) + 1})"
        print_source_info(answers)
        if original_title is None:
            answers.pop("_source_info_title", None)
        else:
            answers["_source_info_title"] = original_title
        for idx, item in enumerate(join_items, start=2):
            joined_answers = join_item_answers(answers, item)
            joined_answers["_source_info_title"] = f"Source file info ({idx}/{len(join_items) + 1})"
            print_source_info(joined_answers)
    else:
        print_source_info(answers)


def step_join_additional_inputs_for_encode(answers: dict[str, Any]) -> None:
    if not output_has_video(answers) or not answers.get("input_path"):
        answers.pop("join_input_items", None)
        answers["_join_question_extra"] = 0
        answers.pop("_join_base_question", None)
        answers.pop("_join_last_question", None)
        return
    existing_items = list(answers.get("join_input_items") or [])
    existing_extra = int(answers.get("_join_question_extra", 0) or 0)
    current_question = int(answers.get("_question_number", 0) or 0)
    resuming_existing_join = bool(existing_items and existing_extra and current_question > existing_extra)
    base_question = int(answers.get("_join_base_question") or (current_question - existing_extra if resuming_existing_join else current_question))
    items: list[dict[str, Any]] = existing_items if resuming_existing_join else []
    if resuming_existing_join:
        resume_question = max(current_question, int(answers.get("_join_last_question") or current_question))
        answers["_question_number"] = resume_question
        if not ask_yes_no(
            question_prompt(answers, "Add another video file?", "y/n", "n"),
            False,
        ):
            answers["join_input_items"] = items
            answers["_join_question_extra"] = existing_extra
            return
        sub_question = resume_question + 1
    else:
        answers.pop("join_input_items", None)
        answers["_join_question_extra"] = 0
        answers["_join_base_question"] = base_question
        if not ask_yes_no(
            question_prompt(answers, "Add another video file to join with this input?", "y/n", "n"),
            False,
        ):
            answers.pop("_join_base_question", None)
            answers.pop("_join_last_question", None)
            return
        sub_question = base_question + 1
    while True:
        answers["_question_number"] = sub_question
        value = ask_required(
            question_prompt(
                answers,
                "Enter additional video file path",
                "drag and drop a video file here or paste a path",
            )
        )
        path = terminal_path(value)
        if not path.exists() or not path.is_file():
            error("File not found. Enter the full file path again.")
            continue
        if paths_same(path, answers["input_path"]) or any(paths_same(path, item["path"]) for item in items):
            error("This video is already selected for joining. Enter a different file.")
            continue
        if looks_like_generated_output_file(path):
            error("This looks like a previously generated FFmWiz output file. It was not added as a join input.")
            continue
        try:
            items.append(join_load_media_item(answers, path))
        except Exception as exc:
            log_exception(f"Join input probe failed: {path}")
            error(str(exc))
            continue
        sub_question += 1
        answers["_question_number"] = sub_question
        if not ask_yes_no(
            question_prompt(answers, "Add another video file?", "y/n", "n"),
            False,
        ):
            break
        sub_question += 1
    answers["_join_last_question"] = sub_question
    answers["_join_question_extra"] = max(0, sub_question - base_question)
    answers["join_input_items"] = items
    note(f"Added {len(items)} additional video input(s) for joining.")


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
        if is_back_value(value):
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
        if is_back_value(value):
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
    if not gpu_available_for_answers(answers):
        answers["use_gpu"] = False
        note("No usable NVIDIA/NVENC GPU was detected. GPU question skipped; CPU mode selected.")
        log_info("User choice: use_gpu=False; reason=GPU unavailable")
        return
    answers["use_gpu"] = ask_yes_no(
        question_prompt(answers, "Use GPU/NVIDIA for decode/filter/encode?", "y/n", "y"),
        True,
    )


def cpu_two_pass_applicable(answers: dict[str, Any]) -> bool:
    if not output_has_video(answers) or answers.get("use_gpu"):
        return False
    if str(answers.get("video_codec") or "").strip().lower() in {"copy", "n"}:
        return False
    if answers.get("join_input_items") or answers.get("separator_points") or answers.get("split_output_paths"):
        return False
    if answers.get("cut_keep_ranges") or answers.get("video_speed_enabled") or answers.get("reverse_video"):
        return False
    video_encoder, _tag, _profile = resolve_video_encoder(answers)
    if video_encoder not in {"libx264", "libx265"}:
        return False
    return True


def step_cpu_two_pass(answers: dict[str, Any]) -> None:
    answers["cpu_two_pass"] = ask_yes_no(
        question_prompt(
            answers,
            "Use two-pass CPU video encoding for closer target bitrate?",
            "y/n; slower, but usually closer to the requested bitrate",
            "y",
        ),
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
                f"y/n; {colored_unified_editor_hint()}",
                "y",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "y"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["_unified_video_editor_used"] = False
            answers["_unified_video_editor_declined"] = True
            answers["_disable_followup_video_gui_prompts"] = True
            answers["crop_enabled"] = False
            answers["crop_values_inline"] = False
            for key in ("crop_top", "crop_left", "crop_right", "crop_bottom"):
                answers.pop(key, None)
            answers["video_speed_enabled"] = False
            answers["reverse_video"] = False
            answers["audio_speed_from_video"] = False
            answers["cut_keep_ranges"] = []
            answers.pop("separator_points", None)
            answers.pop("_unified_separator_points", None)
            return
        if lowered in {"y", "yes"}:
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
            answers["_unified_video_editor_declined"] = False
            answers["_disable_followup_video_gui_prompts"] = False
            answers["_unified_cut_keep_ranges"] = result.get("keep_ranges") or []
            answers["_unified_separator_points"] = result.get("separator_points") or []
            if answers["_unified_separator_points"]:
                answers["separator_points"] = list(answers["_unified_separator_points"])
            else:
                answers.pop("separator_points", None)
            answers["_unified_video_speed"] = result["speed"]
            answers["_unified_reverse_video"] = result["reverse"]
            answers["_unified_include_audio"] = result["include_audio"]
            speed = clamp_speed_factor(result.get("speed", DEFAULT_SPEED_FACTOR))
            reverse = bool(result.get("reverse"))
            answers["video_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
            answers["video_speed_factor"] = speed
            answers["reverse_video"] = reverse
            answers["audio_speed_from_video"] = bool(result.get("include_audio"))
            unified_duration = stream_duration_seconds({}, answers.get("format")) or 0.0
            unified_duration += sum(float(item.get("duration") or 0.0) for item in answers.get("join_input_items") or [])
            keep_ranges = normalize_cut_ranges(result.get("keep_ranges") or [], unified_duration)
            answers["cut_keep_ranges"] = keep_ranges
            if keep_ranges:
                print(paint(format_cut_ranges_for_summary(keep_ranges, get_video_fps(answers), "Cuts (keep ranges)"), Color.LIME))
            _unified_seps = answers.get("_unified_separator_points") or []
            if _unified_seps:
                print(paint(format_split_points_for_summary(_unified_seps, get_video_fps(answers), "Split points"), Color.LIME))
            print(paint("Graphical edits captured.", Color.LIME))
            if answers["video_speed_enabled"]:
                print(
                    paint(
                        f"Applied unified video speed: {speed * 100:.0f}% ({speed:g}x); "
                        f"reverse video: {'yes' if reverse else 'no'}; "
                        f"sync audio: {'yes' if answers['audio_speed_from_video'] else 'no'}",
                        Color.LIME,
                    )
                )
            return
        error("Enter y or n.")


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
        if is_back_value(value):
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
            error("The standalone Crop GUI is archived. Use the Unified Video Editor or enter crop margins inline.")
            continue

        pieces = [piece.strip() for piece in value.split(",")]
        if len(pieces) != 4 or any(not re.fullmatch(r"\d+", piece) for piece in pieces):
            error("Enter y, n, or four integer crop margins: top,left,right,bottom")
            continue
        top, left, right, bottom = [int(piece) for piece in pieces]
        if not set_crop_margins_if_valid(answers, top, left, right, bottom):
            continue
        answers["crop_values_inline"] = True
        return


def step_crop_top(answers: dict[str, Any]) -> None:
    while True:
        value = ask_raw(question_prompt(answers, "Enter crop top px", "integer pixels; zero is allowed", back="back=b, quit=exit"))
        if value.lower().strip() in {"b", "back"}:
            raise Back()
        if not re.fullmatch(r"\d+", value or ""):
            error("Enter a non-negative integer.")
            continue
        if set_single_crop_margin_if_valid(answers, "crop_top", int(value)):
            return


def step_crop_left(answers: dict[str, Any]) -> None:
    while True:
        value = ask_raw(question_prompt(answers, "Enter crop left px", "integer pixels; zero is allowed", back="back=b, quit=exit"))
        if value.lower().strip() in {"b", "back"}:
            raise Back()
        if not re.fullmatch(r"\d+", value or ""):
            error("Enter a non-negative integer.")
            continue
        if set_single_crop_margin_if_valid(answers, "crop_left", int(value)):
            return


def step_crop_right(answers: dict[str, Any]) -> None:
    while True:
        value = ask_raw(question_prompt(answers, "Enter crop right px", "integer pixels; zero is allowed", back="back=b, quit=exit"))
        if value.lower().strip() in {"b", "back"}:
            raise Back()
        if not re.fullmatch(r"\d+", value or ""):
            error("Enter a non-negative integer.")
            continue
        if set_single_crop_margin_if_valid(answers, "crop_right", int(value)):
            return


def step_crop_bottom(answers: dict[str, Any]) -> None:
    while True:
        value = ask_raw(question_prompt(answers, "Enter crop bottom px", "integer pixels; zero is allowed", back="back=b, quit=exit"))
        if value.lower().strip() in {"b", "back"}:
            raise Back()
        if not re.fullmatch(r"\d+", value or ""):
            error("Enter a non-negative integer.")
            continue
        if set_single_crop_margin_if_valid(answers, "crop_bottom", int(value)):
            return


def step_video_bitrate(answers: dict[str, Any]) -> None:
    packet_sizes = get_packet_sizes(answers)
    source = stream_bitrate_kbps(answers["video_streams"][0], answers.get("format"), packet_sizes)
    source_limit, source_limit_label = detected_video_bitrate_limit(answers)

    # First ask: bitrate mode or constant quality (RF/CQ).
    mode_prompt = question_prompt(
        answers,
        "Video quality mode",
        f"{paint('bitrate', Color.OPT_KEY_CYAN)}{paint('=target average kbps', Color.HINT_YELLOW)}; "
        f"{paint('CRF', Color.OPT_KEY_CYAN)}{paint('=constant quality (RF/CQ)', Color.HINT_YELLOW)}",
        "bitrate",
    )
    while True:
        mode_value = ask_raw(mode_prompt).strip().lower()
        if is_back_value(mode_value):
            raise Back()
        if not mode_value or mode_value in {"bitrate", "b", "1"}:
            mode_value = "bitrate"
            break
        if mode_value in {"rf", "crf", "cq", "2", "quality"}:
            mode_value = "rf"
            break
        error("Enter 'bitrate' or 'CRF'.")

    if mode_value == "rf":
        _step_video_constant_quality(answers)
        return

    # Bitrate mode: same as before.
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
        if is_back_value(value):
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
        answers.pop("video_crf", None)
        return


def _step_video_constant_quality(answers: dict[str, Any]) -> None:
    """Ask for a CRF/CQ value for constant-quality encoding."""
    encoder, _, _ = resolve_video_encoder(answers)
    is_nvenc = str(encoder).endswith("_nvenc")

    if is_nvenc:
        label = "CQ"
        range_text = "0-51; 0 = lossless, 19-23 = visually good, 28-35 = smaller files"
        default = "23"
    else:
        label = "CRF"
        range_text = "0-51; 0 = lossless, 18-23 = visually good, 28-35 = smaller files"
        default = "23"

    note(
        f"Constant Quality ({paint(label, Color.CYAN)}) mode: the encoder targets a perceptual quality level.\n"
        f"  Range: {paint(range_text, Color.HINT_YELLOW)}\n"
        f"  Lower = higher quality + larger file. Higher = lower quality + smaller file.\n"
        f"  Decimal values accepted (e.g. {paint('22.5', Color.LIME)}). Typical range for good quality: {paint('18-28', Color.GREEN)}."
    )

    prompt = question_prompt(
        answers,
        f"Enter {label} value",
        f"range {example_text('0-51')}; examples: {example_text('18, 23, 28, 22.5')}",
        default,
    )
    while True:
        value = ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if not value:
            value = default
        try:
            crf = float(value)
        except (TypeError, ValueError):
            error("Enter a number (integer or decimal).")
            continue
        if crf < 0 or crf > 51:
            error("Value must be between 0 and 51.")
            continue
        answers["video_crf"] = crf
        answers.pop("video_bitrate_kbps", None)
        answers.pop("video_bitrate_keep", None)
        log_info(f"User choice: video constant quality {label}={crf}")
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
        if is_back_value(value):
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
        if is_back_value(value):
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
    volume_stats = get_audio_volume_stats(answers)
    print()
    print(paint("Detected audio tracks:", Color.BOLD + Color.BLUE))
    for idx, stream in enumerate(streams):
        size, _ = stream_size_bytes(stream, fmt, packet_sizes)
        labels = duplicate_labels(idx, report) if report else []
        label_text = f" | {' | '.join(labels)}" if labels else ""
        print(
            f"  {paint(stream_title(stream, idx), Color.WHITE)} | "
            f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, idx), Color.MEAN_VOLUME)} | "
            f"{field_text('size', format_bytes(size), Color.LIME)}{label_text}"
        )
    join_items = list(answers.get("join_input_items") or [])
    if join_items:
        print()
        print(paint("Joined input audio tracks", Color.BOLD + Color.BLUE))
        print("  " + paint("The selected track numbers below will be applied to every joined input.", Color.YELLOW))
        for input_pos, item in enumerate(join_items, start=2):
            joined = join_item_answers(answers, item)
            joined_streams = joined.get("audio_streams") or []
            joined_fmt = joined.get("format", {})
            joined_packet_sizes = get_packet_sizes(joined)
            joined_report = detect_duplicate_audio(joined) if joined.get("detect_duplicate_audio", True) and joined_streams else None
            joined_volume = get_audio_volume_stats(joined) if joined_streams else {}
            print("  " + field_text(f"input {input_pos}", Path(item.get("path")).name, Color.WHITE))
            for idx, stream in enumerate(joined_streams):
                size, _ = stream_size_bytes(stream, joined_fmt, joined_packet_sizes)
                labels = duplicate_labels(idx, joined_report) if joined_report else []
                label_text = f" | {' | '.join(labels)}" if labels else ""
                print(
                    f"    {paint(stream_title(stream, idx), Color.WHITE)} | "
                    f"{field_text('mean / max volume', audio_mean_max_volume_field(joined_volume, idx), Color.MEAN_VOLUME)} | "
                    f"{field_text('size', format_bytes(size), Color.LIME)}{label_text}"
                )
            if len(joined_streams) != len(streams):
                warning = f"input {input_pos} has {len(joined_streams)} audio track(s), primary input has {len(streams)}."
                print("    " + paint(warning, Color.YELLOW))

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
        # This prompt uses zero-based audio track numbers, so 0 must remain a
        # valid stream selection. Back is intentionally b/back here.
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
    if is_back_value(value):
        raise Back()
    if not value:
        value = "n" if answers.get("_folder_encode_mode") else default_codec
    if value.lower() == "n":
        value = "copy"
    value = normalize_audio_codec(value, default_codec)
    if loudnorm_transform_enabled(answers) and value.lower() == "copy":
        use_aac = ask_yes_no(
            question_prompt(
                answers,
                "LoudNorm requires audio re-encoding. Use AAC?",
                "y/n",
                "y",
            ),
            True,
        )
        if use_aac:
            value = DEFAULT_AUDIO_CODEC
        else:
            note("LoudNorm disabled because audio remains stream-copy.")
            answers["loudnorm_enabled"] = False
    answers["audio_codec"] = normalize_audio_codec(value, default_codec)


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
        if is_back_value(value):
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


def step_loudnorm(answers: dict[str, Any]) -> None:
    answers.pop("loudnorm_enabled", None)
    answers.pop("loudnorm_target_i", None)
    answers.pop("loudnorm_measured", None)
    selected = selected_audio_streams(answers) if answers.get("audio_streams") else []
    if not selected:
        return
    enabled = ask_yes_no(
        question_prompt(
            answers,
            "Increase / normalize audio loudness with loudnorm?",
            "y/n",
            "n",
        ),
        False,
    )
    if not enabled:
        answers["loudnorm_enabled"] = False
        log_info("User choice: loudnorm_enabled=False")
        return
    log_info("User choice: loudnorm_enabled=True")

    audio_codec = normalize_audio_codec(
        answers.get("audio_codec"),
        default_audio_codec_for_ext(answers.get("output_ext", "")),
    )
    answers["audio_codec"] = audio_codec
    if audio_codec == "copy":
        use_aac = ask_yes_no(
            question_prompt(
                answers,
                "LoudNorm requires audio re-encoding. Use AAC?",
                "y/n",
                "y",
            ),
            True,
        )
        if not use_aac:
            note("LoudNorm disabled because audio remains stream-copy.")
            answers["loudnorm_enabled"] = False
            log_info("LoudNorm measured/applied decision: disabled because user kept audio stream-copy.")
            return
        answers["audio_codec"] = DEFAULT_AUDIO_CODEC
        answers.setdefault("audio_bitrate_kbps", DEFAULT_AUDIO_BITRATE_KBPS)

    # Ask whether to measure current loudness (two-pass) or skip to manual target.
    measure = ask_yes_no(
        question_prompt(
            answers,
            "Measure current audio loudness for two-pass normalization?",
            f"{paint('y', Color.OPT_KEY_CHARTREUSE)}{paint('=measure (more accurate)', Color.HINT_YELLOW)}; "
            f"{paint('n', Color.OPT_KEY_CHARTREUSE)}{paint('=skip to manual target (faster)', Color.HINT_YELLOW)}",
            "y",
        ),
        True,
    )

    measured: dict[str, float] | None = None
    if measure:
        audio_index = selected[0]
        ffmpeg = str(answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg")
        input_path = Path(answers["input_path"])
        while True:
            note(f"Measuring current loudness on audio track {audio_index}...")
            total_duration = stream_duration_seconds({}, answers.get("format"))
            measured = probe_loudnorm_measurement(ffmpeg, input_path, audio_index, total_duration=total_duration)
            if measured is not None:
                print_loudnorm_stats(measured)
                break
            error("LoudNorm measurement failed.")
            action = ask_raw(
                question_prompt(
                    answers,
                    "LoudNorm measurement failed. Choose action",
                    "r=retry; c=continue without loudnorm; m=manual single-pass loudnorm",
                    "c",
                )
            ).strip().lower()
            if is_back_value(action):
                raise Back()
            if not action or action == "c":
                answers["loudnorm_enabled"] = False
                return
            if action == "m":
                measured = None
                break
            if action != "r":
                error("Enter r, c, or m.")

    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Enter target Integrated Loudness I in LUFS",
                "examples: -16 general video, -18 safer/lower, -14 louder",
                loudnorm_number(LOUDNORM_DEFAULT_TARGET_I),
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = loudnorm_number(LOUDNORM_DEFAULT_TARGET_I)
        try:
            target_i = parse_loudnorm_target(value)
        except ValueError as exc:
            error(str(exc))
            continue
        answers["loudnorm_enabled"] = True
        answers["loudnorm_target_i"] = target_i
        if measured is not None:
            answers["loudnorm_measured"] = measured
        else:
            answers.pop("loudnorm_measured", None)
        log_info(
            f"User choice: loudnorm_target_i={target_i}; confirmed=yes; "
            f"two_pass={'yes' if measured is not None else 'no'}"
        )
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


def step_source_extra_policy(answers: dict[str, Any]) -> None:
    features = source_extra_preservation_features(answers)
    if not features:
        answers["keep_source_metadata"] = True
        answers["keep_source_chapters"] = True
        answers["keep_source_subtitles"] = True
        answers["keep_source_data_streams"] = True
        answers["keep_source_extra_video_streams"] = True
        answers["keep_embedded_attachments"] = False
        return
    print()
    print(paint("Detected source metadata / extra streams:", Color.BOLD + Color.LIGHT_BLUE))
    for feature in features:
        print("  " + paint(feature, Color.WHITE))
    keep = ask_yes_no(
        question_prompt(
            answers,
            "Keep source metadata, chapters, extra video/subtitle/data streams, and embedded fonts/attachments?",
            "y/n; n removes metadata, chapters, extra source video streams, source subtitles, data streams, and embedded font/attachment streams",
            "y",
        ),
        True,
    )
    answers["keep_source_metadata"] = keep
    answers["keep_source_chapters"] = keep
    answers["keep_source_subtitles"] = keep
    answers["keep_source_data_streams"] = keep
    answers["keep_source_extra_video_streams"] = keep
    if not keep:
        answers["subtitle_tracks"] = []
        answers["keep_embedded_attachments"] = False
        log_info(
            "User choice: keep_source_extras=False; "
            "metadata=no; chapters=no; extra_video_streams=no; subtitles=no; data_streams=no; embedded_attachments=no"
        )
        return

    attachment_count = len(embedded_attachment_streams(answers))
    keep_attachments = False
    if attachment_count:
        if output_supports_embedded_attachments(answers):
            keep_attachments = True
        else:
            note(
                "Embedded font/attachment streams can only be kept reliably in MKV output here. "
                f"Current output format is {answers.get('output_ext')}."
            )
            change_to_mkv = ask_yes_no(
                question_prompt(
                    answers,
                    "Change output format to MKV so embedded font/attachment streams can be kept?",
                    "y/n; otherwise metadata/chapters/subtitles are kept but embedded attachments are dropped",
                    "n",
                ),
                False,
            )
            if change_to_mkv:
                answers["output_ext"] = "mkv"
                keep_attachments = True
    answers["keep_embedded_attachments"] = keep_attachments
    log_info(
        "User choice: keep_source_extras=True; "
        f"metadata=yes; chapters=yes; extra_video_streams=yes; subtitles=yes; data_streams=yes; "
        f"embedded_attachments={keep_attachments}; output_ext={answers.get('output_ext')}"
    )


def embedded_attachment_display_line(stream: dict[str, Any], relative_index: int) -> str:
    filename = stream_tag_value(stream, "filename", "")
    mimetype = stream_tag_value(stream, "mimetype", "")
    title = stream_tag_value(stream, "title", "")
    pieces = [
        f"{relative_index}: stream #{stream.get('index', '?')}",
        f"codec={stream.get('codec_name', 'unknown')}",
        f"kind={media_info_attachment_kind(stream)}",
    ]
    if filename:
        pieces.append(f"filename={filename}")
    if mimetype:
        pieces.append(f"mimetype={mimetype}")
    if title:
        pieces.append(f"title={title}")
    return " | ".join(pieces)


def step_embedded_attachments(answers: dict[str, Any]) -> None:
    streams = embedded_attachment_streams(answers)
    print()
    print(paint(f"Detected {len(streams)} embedded attachment(s):", Color.BOLD + Color.PINK))
    for idx, stream in enumerate(streams):
        print("  " + paint(embedded_attachment_display_line(stream, idx), Color.WHITE))
    if not output_supports_embedded_attachments(answers):
        note(
            "Embedded font/attachment streams can only be kept reliably in MKV output here. "
            f"Current output format is {answers.get('output_ext')}."
        )
        keep = ask_yes_no(
            question_prompt(
                answers,
                "Change output format to MKV and keep embedded font/attachment streams?",
                "y/n; required if you want embedded MKV fonts or other attachment streams copied",
                "n",
            ),
            False,
        )
        if keep:
            answers["output_ext"] = "mkv"
        answers["keep_embedded_attachments"] = keep
        log_info(
            f"User choice: keep_embedded_attachments={keep}; "
            f"attachment_count={len(streams)}; output_ext={answers.get('output_ext')}"
        )
        return
    keep = ask_yes_no(
        question_prompt(
            answers,
            "Keep embedded font/attachment streams in the encode?",
            "y/n; MKV output only; copies attachment streams without re-encoding",
            "n",
        ),
        False,
    )
    answers["keep_embedded_attachments"] = keep
    log_info(
        f"User choice: keep_embedded_attachments={keep}; "
        f"attachment_count={len(streams)}; output_ext={answers.get('output_ext')}"
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
    all_input_paths = [input_path] + [Path(item["path"]) for item in answers.get("join_input_items") or [] if item.get("path")]
    output_path = resolve_output_collision_against_inputs(output_path, all_input_paths, collision_suffix)
    log_info(f"Resolved output path: {output_path}")
    return output_path


def build_separator_base_output_path(answers: dict[str, Any]) -> Path:
    input_path: Path = answers["input_path"]
    output_location: Path = answers["output_location"]
    output_ext = answers["output_ext"]
    if answers.get("output_name_stem"):
        return output_location / f"{sanitize_output_stem(answers['output_name_stem'])}.{output_ext}"
    if output_location.suffix:
        output_path = output_location.with_suffix("." + output_ext)
        return output_path.with_name(f"{sanitize_output_stem(output_path.stem)}{output_path.suffix}")
    return output_location / f"{sanitize_output_stem(input_path.stem)}.{output_ext}"


def separator_output_path(answers: dict[str, Any], index: int) -> Path:
    base = build_separator_base_output_path(answers)
    suffix = base.suffix or ("." + str(answers.get("output_ext") or "mp4").lstrip("."))
    return unique_numbered_path(base.with_name(f"{sanitize_output_stem(base.stem)}_Part{int(index):02d}{suffix}"))


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
        or answers.get("separator_points")
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
    # AR-preserving resize with padding cannot be done purely in CUDA; fall back
    # to the complex graph path so CPU scale+pad filters are used with NVENC encode.
    if _cuda_fast_path_needs_ar_padding(answers):
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
        and not answers.get("separator_points")
        and not video_speed_transform_enabled(answers)
    )


def _cuda_fast_path_needs_ar_padding(answers: dict[str, Any]) -> bool:
    """Return True when the requested resize requires a pad filter
    and therefore cannot use the pure scale_cuda fast path.
    Only 'box' mode needs padding because the target canvas may differ from the
    AR-preserving scaled dimensions. Preset/height/width modes already compute
    AR-preserving dimensions where pad is a no-op."""
    resolution = answers.get("resolution", "n")
    if resolution is None or resolution == "n":
        return False
    if resize_mode_is_stretch(answers):
        return False
    if isinstance(resolution, dict) and resolution.get("mode") == "box":
        return True
    return False


def should_use_cuda_decode_for_complex_graph(
    answers: dict[str, Any],
    video_encoder: str | None,
    using_cuda_fast_path: bool,
) -> bool:
    if using_cuda_fast_path:
        return False
    if not (
        answers.get("use_gpu")
        and output_has_video(answers)
        and video_encoder
        and video_encoder != "copy"
        and str(video_encoder).endswith("_nvenc")
    ):
        return False
    cut_ranges = list(answers.get("cut_keep_ranges") or [])
    return bool(
        answers.get("_join_complex_graph")
        or answers.get("_force_cpu_video_filter")
        or len(cut_ranges) > 1
        or video_speed_transform_enabled(answers)
        or video_filters_required(answers)
    )


def append_cuda_decode_args_for_input(cmd: list[str], answers: dict[str, Any]) -> None:
    cmd.extend(["-hwaccel", "cuda", "-hwaccel_device", str(GPU_DEVICE_INDEX)])


def build_cuda_video_filter(answers: dict[str, Any]) -> str | None:
    resolution = answers.get("resolution", "n")
    scale_dimensions = resolve_scale_dimensions(answers, resolution)
    cuda_format = cuda_pixel_format_for_output(answers)
    if scale_dimensions:
        width, height = scale_dimensions
        return (
            f"scale_cuda=w={width}:h={height}:format={cuda_format}:"
            "interp_algo=bicubic:passthrough=0:reset_sar=1"
        )
    return f"scale_cuda=format={cuda_format}:passthrough=0:reset_sar=1"


def build_cpu_video_filter(answers: dict[str, Any]) -> str | None:
    filters: list[str] = []
    if answers.get("crop_enabled"):
        left = answers["crop_left"]
        right = answers["crop_right"]
        top = answers["crop_top"]
        bottom = answers["crop_bottom"]
        filters.append(f"crop=iw-{left}-{right}:ih-{top}-{bottom}:{left}:{top}:exact=1")

    if answers.get("fps") is not None:
        filters.append(f"fps={answers['fps']}")

    resolution = answers.get("resolution", "n")
    scale_dimensions = resolve_scale_dimensions(answers, resolution)
    scale_resets_sar = False
    if scale_dimensions:
        width, height = scale_dimensions
        is_stretch = resize_mode_is_stretch(answers)
        if is_stretch:
            # Exact stretch: force the requested dimensions regardless of AR.
            filters.append(f"scale={width}:{height}")
            log_info(f"Resize mode: Stretch; scale={width}:{height}")
        else:
            # AR-preserving: always use force_original_aspect_ratio=decrease so
            # the content fits inside the target canvas without distortion, then
            # pad to the exact canvas dimensions. When the source AR matches
            # the target, FFmpeg produces the exact dimensions and the pad is a
            # no-op. This approach handles all cases uniformly.
            # reset_sar=1 inside the scale filter ensures output pixels are
            # square, making a trailing setsar=1 unnecessary.
            sar = source_sar(answers)
            crop_w, crop_h = cropped_source_size(answers)
            display_w, display_h = cropped_display_size(answers)
            filters.append(
                f"scale={width}:{height}:"
                f"force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1"
            )
            filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
            scale_resets_sar = True
            log_info(
                f"Resize mode: Preserve; "
                f"source_coded={crop_w}x{crop_h}; SAR={sar:.4f}; "
                f"display={display_w}x{display_h}; target={width}x{height}; "
                f"upscaling={'yes' if max(width, height) > max(display_w, display_h) else 'no'}"
            )

    if video_speed_transform_enabled(answers):
        filters.append(build_video_speed_filter(encode_video_speed_factor(answers), bool(answers.get("reverse_video"))))

    # When crop is active but no resize step guarantees even dimensions,
    # add a compatibility pad that rounds to even width/height for encoders.
    if answers.get("crop_enabled") and not scale_dimensions:
        filters.append("pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0")

    if FORCE_SAR and not scale_resets_sar:
        filters.append(f"setsar={FORCE_SAR}")

    filters.append(f"format={cpu_pixel_format_for_output(answers)}")
    return ",".join(filters) if filters else None


def build_cpu_fallback_from_cuda_filter(answers: dict[str, Any]) -> str | None:
    cpu_filter = build_cpu_video_filter(answers)
    if not cpu_filter:
        return None
    cuda_format = cuda_pixel_format_for_output(answers)
    return f"hwdownload,format={cuda_format},{cpu_filter},format={cuda_format},hwupload_cuda"


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
            source_labels: list[str]
            if len(keep_ranges) > 1:
                source_labels = [f"acut{pos}_src{range_idx}" for range_idx in range(len(keep_ranges))]
                parts.append(
                    f"[{current_label}]asplit={len(keep_ranges)}"
                    f"{''.join(f'[{label}]' for label in source_labels)}"
                )
                log_info(
                    f"Inserted asplit={len(keep_ranges)} for multi-range audio trim from "
                    f"[{current_label}]."
                )
            else:
                source_labels = [current_label]
            range_labels: list[str] = []
            for range_idx, (start, end) in enumerate(keep_ranges):
                label = f"acut{pos}_{range_idx}"
                range_labels.append(f"[{label}]")
                parts.append(
                    f"[{source_labels[range_idx]}]atrim=start={start:.6f}:end={end:.6f},"
                    f"asetpts=PTS-STARTPTS[{label}]"
                )
            if len(keep_ranges) > 1:
                cut_label = f"acut{pos}"
                parts.append(f"{''.join(range_labels)}concat=n={len(keep_ranges)}:v=0:a=1[{cut_label}]")
                current_label = cut_label
            else:
                current_label = f"acut{pos}_0"
        out_label = f"aout{pos}"
        if audio_speed_transform_enabled(answers) or loudnorm_transform_enabled(answers):
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


def append_video_bitrate_args(
    cmd: list[str],
    answers: dict[str, Any],
    bitrate_kbps: int,
    stream_spec: str = ":v",
) -> None:
    mode = video_bitrate_mode(answers)
    if mode == "strict_size":
        maxrate = bitrate_kbps
        bufsize = bitrate_kbps * 2
    else:
        maxrate = bitrate_kbps * 2
        bufsize = bitrate_kbps * 4
    cmd.extend([
        f"-b{stream_spec}", f"{bitrate_kbps}k",
        f"-maxrate{stream_spec}", f"{maxrate}k",
        f"-bufsize{stream_spec}", f"{bufsize}k",
    ])


def ps_quote(arg: str) -> str:
    if arg == "":
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_./:+=-]+", arg):
        return arg
    return "'" + arg.replace("'", "''") + "'"


def command_to_powershell(args: list[str]) -> str:
    return " ".join(ps_quote(arg) for arg in args)


def append_single_input_split_outputs(
    cmd: list[str],
    answers: dict[str, Any],
    output_path: Path,
    video_encoder: str,
    tag: str | None,
    profile: str | None,
    audio_indices: list[int],
    audio_transform_active: bool,
    multi_cut: bool,
    audio_for_cut: int | None,
) -> bool:
    source_duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    final_duration = final_processed_duration_for_splits(answers, source_duration)
    split_points = normalize_separator_points(answers.get("separator_points"), final_duration)
    if not split_points:
        return False
    filters: list[str] = []
    audio_labels: list[str] = []
    if multi_cut:
        filters.extend(build_cut_filter_complex(answers, list(answers.get("cut_keep_ranges") or []), audio_for_cut).split(";"))
        video_label = "v"
        if audio_for_cut is not None:
            audio_labels.append("a")
    else:
        video_filter = build_cpu_video_filter(answers) or "null"
        filters.append(f"[0:v:0]{video_filter}[vbase]")
        video_label = "vbase"
        if audio_indices:
            if audio_transform_active:
                audio_fc, labels = build_audio_transform_filter_complex(answers, audio_indices)
                filters.extend(audio_fc.split(";"))
                audio_labels.extend(labels)
            else:
                for pos, audio_index in enumerate(audio_indices):
                    label = f"abase{pos}"
                    filters.append(f"[0:a:{audio_index}]asetpts=PTS-STARTPTS[{label}]")
                    audio_labels.append(label)
    filters.append(f"[{video_label}]setpts=PTS-STARTPTS[vfinal]")
    final_audio_labels: list[str] = []
    for pos, label in enumerate(audio_labels):
        final_label = f"afinal{pos}"
        filters.append(f"[{label}]asetpts=PTS-STARTPTS[{final_label}]")
        final_audio_labels.append(final_label)
    video_outputs, audio_outputs_by_part, split_intervals = append_final_split_filters(
        filters,
        "vfinal",
        final_audio_labels,
        split_points,
        final_duration,
        "s",
        float(answers.get("fps") or get_video_fps(answers) or 0.0),
    )
    output_paths = split_part_output_paths(output_path, len(video_outputs), [Path(answers["input_path"])])
    answers["split_output_paths"] = output_paths
    answers["split_part_intervals"] = split_intervals
    answers["output_path"] = output_paths[0]

    # Prepare per-part chapter remapping if timeline is modified and chapters exist.
    split_chapter_plans: list[dict[str, Any]] = []
    split_chapter_metadata_paths: list[Path] = []
    if source_chapters_keep_enabled(answers):
        speed_factor = encode_video_speed_factor(answers) if video_speed_transform_enabled(answers) else 1.0
        for part_idx, interval in enumerate(split_intervals):
            plan = remap_chapters_for_encode(answers, speed_factor=speed_factor, part_interval=interval)
            split_chapter_plans.append(plan)
            if plan.get("mode") == "metadata" and plan.get("chapters"):
                chapter_temp_dir = answers.get("_chapter_metadata_temp_dir")
                if not chapter_temp_dir:
                    chapter_temp_dir = tempfile.mkdtemp(prefix="ffmwiz_split_chapters_")
                    answers["_chapter_metadata_temp_dir"] = chapter_temp_dir
                metadata_path = write_encode_chapter_metadata(plan, Path(chapter_temp_dir), suffix=f"_part{part_idx + 1:02d}")
                split_chapter_metadata_paths.append(metadata_path)
            else:
                split_chapter_metadata_paths.append(None)

    # Add chapter metadata inputs (input index 1, 2, ... for each part that has chapters).
    chapter_input_base = 1  # Input 0 is the main source file.
    metadata_input_count = 0
    part_chapter_input_indices: list[int | None] = []
    for metadata_path in split_chapter_metadata_paths:
        if metadata_path is not None:
            cmd.extend(["-i", str(metadata_path)])
            part_chapter_input_indices.append(chapter_input_base + metadata_input_count)
            metadata_input_count += 1
        else:
            part_chapter_input_indices.append(None)

    cmd.extend(["-filter_complex", ";".join(filters)])
    for part_idx, part_output in enumerate(output_paths):
        cmd.extend(["-map", f"[{video_outputs[part_idx]}]"])
        for audio_label in audio_outputs_by_part[part_idx]:
            cmd.extend(["-map", f"[{audio_label}]"])
        attachments_mapped = append_embedded_attachment_maps(cmd, answers)
        data_mapped = append_source_data_maps(cmd, answers)

        # Per-part chapter handling for splits.
        if not output_has_video(answers):
            pass
        else:
            cmd.extend(["-map_metadata", "0" if source_metadata_keep_enabled(answers) else "-1"])
            if not source_chapters_keep_enabled(answers):
                cmd.extend(["-map_chapters", "-1"])
            elif part_chapter_input_indices and part_chapter_input_indices[part_idx] is not None:
                cmd.extend(["-map_chapters", str(part_chapter_input_indices[part_idx])])
                log_info(f"Chapters: Part {part_idx + 1} uses remapped metadata input {part_chapter_input_indices[part_idx]}")
            else:
                cmd.extend(["-map_chapters", "-1"])

        append_negative_stream_options(cmd, answers, True, [], data_mapped)
        append_video_encode_options(cmd, answers, video_encoder, tag, profile)
        append_audio_encode_options(cmd, answers, bool(audio_outputs_by_part[part_idx]))
        append_clear_reencoded_stream_stat_metadata(
            cmd,
            answers,
            video_output_count=1 if video_encoder != "copy" else 0,
            audio_output_count=len(audio_outputs_by_part[part_idx]) if audio_outputs_by_part[part_idx] else 0,
            subtitle_output_count=0,
        )
        if attachments_mapped:
            append_embedded_attachment_codec_options(cmd, answers)
        if data_mapped:
            append_source_data_codec_options(cmd, answers)
        append_container_options(cmd, answers["output_ext"])
        cmd.append(str(part_output))
    log_info(
        "Split final output into parts: "
        + ", ".join(
            f"Part {idx + 1:02d} {seconds_to_ffmpeg_time(start)}->{seconds_to_ffmpeg_time(end)}"
            for idx, (start, end) in enumerate(split_intervals)
        )
    )
    return True


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
    if answers.get("use_gpu") and answers.get("gpu_available") is False:
        answers["use_gpu"] = False
        log_info("GPU disabled automatically because no usable NVIDIA/NVENC GPU was detected.")

    has_video = output_has_video(answers)
    video_encoder = None
    tag = None
    profile = None
    use_cuda_fast_path = False
    use_cuda_decode_complex = False

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
        video_encoder, tag, profile = enforce_bit_depth_compatible_video_encoder(answers, video_encoder, tag, profile)

        use_cuda_fast_path = can_use_cuda_fast_path(answers, video_encoder)
        use_cuda_decode_complex = should_use_cuda_decode_for_complex_graph(answers, video_encoder, use_cuda_fast_path)
        if answers.get("use_gpu") and video_encoder != "copy" and not str(video_encoder).endswith("_nvenc"):
            note("The selected video encoder is not NVENC, so CPU decode/filter/encode will be used for video.")
        elif multi_cut and answers.get("use_gpu") and str(video_encoder).endswith("_nvenc"):
            log_info("Multiple cut ranges use CPU trim/concat filter_complex; NVENC encode remains enabled.")
        elif use_cuda_decode_complex:
            log_info("Complex CPU filter graph uses CUDA/NVDEC input decode and NVENC final encode.")
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
        elif use_cuda_decode_complex:
            append_cuda_decode_args_for_input(cmd, answers)

    if single_cut:
        start, end = cut_keep_ranges[0]
        if start > 0:
            cmd.extend(["-ss", f"{start:.6f}"])

    cmd.extend(["-i", str(input_path)])
    if single_cut:
        start, end = cut_keep_ranges[0]
        cmd.extend(["-t", f"{max(0.0, end - start):.6f}"])

    # Chapter remapping: if timeline is modified and source has chapters,
    # generate a metadata file and add it as a second input so -map_chapters
    # can reference it. The metadata file path is stored for later cleanup.
    if (
        has_video
        and source_chapters_keep_enabled(answers)
        and timeline_is_modified(answers)
        and not answers.get("separator_points")  # Split path handles its own chapters.
    ):
        speed_factor = encode_video_speed_factor(answers) if video_speed_transform_enabled(answers) else 1.0
        chapter_plan = remap_chapters_for_encode(answers, speed_factor=speed_factor)
        if chapter_plan.get("mode") == "metadata" and chapter_plan.get("chapters"):
            chapter_temp_dir = tempfile.mkdtemp(prefix="ffmwiz_encode_chapters_")
            answers["_chapter_metadata_temp_dir"] = chapter_temp_dir
            metadata_path = write_encode_chapter_metadata(chapter_plan, Path(chapter_temp_dir))
            cmd.extend(["-i", str(metadata_path)])
            answers["_chapter_metadata_input_index"] = 1
            log_info(f"Chapters: injected metadata input at index 1 ({metadata_path})")

    # Determine audio mapping. When multi-range cuts are active, only one audio
    # output stream is produced by the filter_complex concat. Pick the first
    # selected audio in that path.
    if answers.get("audio_speed_from_video") and answers.get("audio_streams"):
        audio_indices = (
            selected_audio_streams(answers)
            if "audio_tracks" in answers
            else list(range(len(answers.get("audio_streams") or [])))
        )
    else:
        audio_indices = selected_audio_streams(answers) if answers.get("audio_streams") else []
    audio_transform_active = bool(audio_indices) and audio_transform_enabled(answers)
    if multi_cut and audio_cut_transform_enabled(answers):
        note("Audio waveform cuts are skipped when video multi-range cuts are active.")
        audio_transform_active = audio_speed_transform_enabled(answers) or loudnorm_transform_enabled(answers)
    audio_for_cut: int | None = None
    if multi_cut and audio_indices:
        audio_for_cut = audio_indices[0]
        if len(audio_indices) > 1:
            note(
                "Cuts active: only the first selected audio track survives the "
                f"filter_complex concat. Using stream index 0:a:{audio_for_cut}."
            )
        audio_indices = [audio_for_cut]

    full_source_map = can_use_full_source_map_for_simple_encode(
        answers,
        audio_indices,
        audio_transform_active,
        multi_cut,
    )

    if (
        has_video
        and video_encoder
        and video_encoder != "copy"
        and answers.get("separator_points")
        and append_single_input_split_outputs(
            cmd,
            answers,
            output_path,
            video_encoder,
            tag,
            profile,
            audio_indices,
            audio_transform_active,
            multi_cut,
            audio_for_cut,
        )
    ):
        return cmd

    extra_video_count = 0
    if full_source_map:
        cmd.extend(["-map", "0"])
        log_info("Full source stream map enabled for simple encode: -map 0")
    elif has_video:
        if multi_cut:
            cmd.extend(["-map", "[v]"])
        else:
            cmd.extend(["-map", "0:v:0"])
            extra_video_count = append_additional_source_video_maps(cmd, answers)

    if multi_cut and audio_for_cut is not None:
        cmd.extend(["-map", "[a]"])
    elif full_source_map:
        pass
    elif audio_transform_active and not multi_cut:
        pass
    elif not multi_cut:
        for audio_index in audio_indices:
            cmd.extend(["-map", f"0:a:{audio_index}"])

    subtitle_indices = [] if full_source_map else (
        selected_subtitle_streams(answers)
        if has_video and source_subtitles_keep_enabled(answers) and answers.get("subtitle_streams")
        else []
    )
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
    attachments_mapped = embedded_attachment_keep_enabled(answers) if full_source_map else append_embedded_attachment_maps(cmd, answers)
    data_mapped = bool(source_data_streams(answers) and source_data_keep_enabled(answers)) if full_source_map else append_source_data_maps(cmd, answers)
    append_source_metadata_chapter_options(cmd, answers)

    if not full_source_map:
        append_negative_stream_options(cmd, answers, has_video, subtitle_indices, data_mapped)

    if has_video and video_encoder:
        video_bitrate = answers.get("video_bitrate_kbps")
        if video_encoder == "copy":
            cmd.extend(["-c:v", "copy"])
        else:
            if full_source_map:
                cmd.extend(["-c", "copy"])
            if multi_cut:
                fc = build_cut_filter_complex(answers, cut_keep_ranges, audio_for_cut)
                cmd.extend(["-filter_complex", fc])
            else:
                video_filter = build_video_filter(answers, use_gpu_filtering=use_cuda_fast_path)
                if video_filter:
                    cmd.extend(["-filter:v:0" if (extra_video_count or full_source_map) else "-filter:v", video_filter])
            if use_cuda_fast_path and answers.get("fps") is not None:
                if extra_video_count or full_source_map:
                    cmd.extend(["-r:v:0", str(answers["fps"]), "-fps_mode:v:0", "cfr"])
                else:
                    cmd.extend(["-r:v", str(answers["fps"]), "-fps_mode:v", "cfr"])
            cmd.extend(["-c:v:0" if full_source_map else "-c:v", video_encoder])

            if video_encoder.endswith("_nvenc"):
                cmd.extend(["-preset", NVENC_PRESET, "-tune", NVENC_TUNE, "-rc", NVENC_RC])
                append_nvenc_multipass_args(cmd, answers, video_encoder)
                if "hevc" in video_encoder:
                    cmd.extend(["-profile:v:0" if full_source_map else "-profile:v", hevc_profile_for_output(answers, profile)])
            elif video_encoder in {"libx264", "libx265"}:
                cmd.extend(["-preset", CPU_PRESET])
                if video_encoder == "libx265":
                    cmd.extend(["-profile:v:0" if full_source_map else "-profile:v", hevc_profile_for_output(answers, "main")])

            if video_bitrate:
                append_video_bitrate_args(cmd, answers, int(video_bitrate), ":v:0" if full_source_map else ":v")
            elif answers.get("video_crf") is not None:
                crf_value = answers["video_crf"]
                stream_spec = ":v:0" if full_source_map else ":v"
                if video_encoder.endswith("_nvenc"):
                    # NVENC constant quality: use constqp rc with -cq:v
                    cmd[cmd.index("-rc") + 1] = "constqp"
                    cmd.extend([f"-cq{stream_spec}", str(int(round(crf_value))), f"-b{stream_spec}", "0"])
                    log_info(f"NVENC constant quality: -rc constqp -cq{stream_spec} {int(round(crf_value))}")
                else:
                    # CPU encoder: -crf
                    cmd.extend([f"-crf", f"{crf_value:g}"])
                    log_info(f"CPU encoder constant quality: -crf {crf_value:g}")

            cmd.extend(["-color_range:v:0", COLOR_RANGE])

            if tag and answers["output_ext"].lower() in MP4_LIKE_EXTS:
                cmd.extend(["-tag:v:0" if full_source_map else "-tag:v", tag])

            if extra_video_count:
                append_additional_source_video_codec_options(cmd, extra_video_count)

    audio_codec_for_stats: str | None = None
    if audio_indices:
        if audio_transform_active and not multi_cut:
            audio_fc, audio_labels = build_audio_transform_filter_complex(answers, audio_indices)
            cmd.extend(["-filter_complex", audio_fc])
            for label in audio_labels:
                cmd.extend(["-map", f"[{label}]"])
        audio_codec = normalize_audio_codec(
            answers.get("audio_codec"),
            default_audio_codec_for_ext(answers.get("output_ext", "")),
        )
        answers["audio_codec"] = audio_codec
        audio_codec_for_stats = audio_codec
        if audio_transform_active and audio_codec == "copy":
            note("Audio copy cannot be used with audio filters such as speed/reverse, waveform cuts, or LoudNorm. AAC was selected for audio.")
            audio_codec = DEFAULT_AUDIO_CODEC
            answers["audio_codec"] = audio_codec
            audio_codec_for_stats = audio_codec
        if answers.get("output_ext", "").lower() == "webm" and audio_codec not in {"copy", "libopus", "libvorbis"}:
            note("WebM audio was changed to libopus for container compatibility.")
            audio_codec = "libopus"
            answers["audio_codec"] = audio_codec
            audio_codec_for_stats = audio_codec
        if audio_codec == "copy":
            cmd.extend(["-c:a", "copy"])
        else:
            if audio_codec == "aac":
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

    output_is_processed = bool(has_video and video_encoder and video_encoder != "copy")
    output_is_processed = output_is_processed or bool(audio_indices and audio_codec_for_stats and audio_codec_for_stats != "copy")
    output_is_processed = output_is_processed or bool(subtitle_indices and answers["output_ext"].lower() in MP4_LIKE_EXTS)
    if output_is_processed:
        video_output_count_for_stats = 0
        if has_video:
            video_output_count_for_stats = len(answers.get("video_streams") or []) if full_source_map else 1 + extra_video_count
        audio_output_count_for_stats = 0
        if audio_indices:
            audio_output_count_for_stats = len(answers.get("audio_streams") or []) if full_source_map else len(audio_indices)
        subtitle_output_count_for_stats = len(answers.get("subtitle_streams") or []) if full_source_map else len(subtitle_indices)
        append_clear_reencoded_stream_stat_metadata(
            cmd,
            answers,
            video_output_count=video_output_count_for_stats,
            audio_output_count=audio_output_count_for_stats,
            subtitle_output_count=subtitle_output_count_for_stats,
        )

    if subtitle_indices:
        if answers["output_ext"].lower() in MP4_LIKE_EXTS:
            cmd.extend(["-c:s", "mov_text"])
        else:
            cmd.extend(["-c:s", "copy"])
    if attachments_mapped:
        append_embedded_attachment_codec_options(cmd, answers)
    if data_mapped:
        append_source_data_codec_options(cmd, answers)

    if answers["output_ext"].lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])

    cmd.append(str(output_path))
    return cmd


def build_separator_job_specs(answers: dict[str, Any]) -> list[dict[str, Any]]:
    duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    segments = separator_ranges(answers.get("separator_points"), duration)
    if len(segments) <= 1:
        return []
    source_keep_ranges = normalize_cut_ranges(list(answers.get("cut_keep_ranges") or []), duration)
    specs: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        keep_ranges = intersect_keep_ranges_with_segment(source_keep_ranges, segment, duration)
        if not keep_ranges:
            log_info(f"Split part {index} skipped because cuts remove the whole part: {segment}")
            continue
        output_path = separator_output_path(answers, index)
        job_answers = dict(answers)
        job_answers["output_location"] = output_path.parent
        job_answers["output_name_stem"] = output_path.stem
        job_answers["output_ext"] = output_path.suffix.lstrip(".") or str(answers.get("output_ext") or "mp4")
        job_answers["output_collision_suffix"] = ""
        job_answers["cut_keep_ranges"] = keep_ranges
        job_answers.pop("separator_points", None)
        job_answers.pop("separator_jobs", None)
        job_answers.pop("cmd", None)
        job_answers.pop("output_path", None)
        cmd = build_ffmpeg_command(job_answers)
        specs.append(
            {
                "index": index,
                "segment": segment,
                "keep_ranges": keep_ranges,
                "output_path": job_answers["output_path"],
                "cmd": cmd,
                "answers": job_answers,
            }
        )
    return specs


def print_separator_summary(specs: list[dict[str, Any]]) -> None:
    if not specs:
        return
    print()
    print(paint("Split output plan:", Color.BOLD + Color.LIGHT_BLUE))
    for spec in specs:
        start, end = spec["segment"]
        print(
            "  "
            + field_text(f"#{spec['index']}", f"{seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}", Color.CYAN)
            + " | "
            + field_text("output", spec["output_path"], Color.LIME)
        )


def run_separator_main_encode(answers: dict[str, Any]) -> tuple[int, float]:
    specs = build_separator_job_specs(answers)
    if not specs:
        return run_ffmpeg_with_progress(
            answers["cmd"],
            total_duration=None,
            label="FFmpeg encode",
        )
    note(f"Split mode will write {len(specs)} output file(s) one at a time.")
    started_at = time.perf_counter()
    failures = 0
    completed = 0
    for position, spec in enumerate(specs, start=1):
        job_answers = spec["answers"]
        start, end = spec["segment"]
        print()
        print(paint(f"Split part [{position}/{len(specs)}]: {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}", Color.BOLD + Color.LIGHT_BLUE))
        if reverse_video_needs_segmented_main_encode(job_answers):
            rc, _ = run_segmented_reverse_main_encode(job_answers)
        else:
            rc, _ = run_ffmpeg_with_progress(
                spec["cmd"],
                total_duration=max(0.001, total_keep_duration(job_answers.get("cut_keep_ranges") or [])),
                label=f"Split part {position}/{len(specs)}",
            )
        if rc == 0:
            completed += 1
            note(f"Finished {Path(spec['output_path']).name}")
        else:
            failures += 1
            error(f"Failed Split part {position} with exit code {rc}.")
    elapsed = time.perf_counter() - started_at
    if failures:
        error(f"Split mode completed with {completed} success(es) and {failures} failure(s).")
        return 1, elapsed
    note(f"Split mode completed successfully: {completed} file(s).")
    return 0, elapsed


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
    cmd.extend(["-c:v", "libx264", "-preset", CPU_PRESET, "-crf", "18", "-pix_fmt", cpu_pixel_format_for_output(answers)])
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
    cmd.extend(["-c:v", "libx264", "-preset", CPU_PRESET, "-crf", "18", "-pix_fmt", cpu_pixel_format_for_output(answers)])
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
        source_labels = [f"acut_src{idx}" for idx in range(len(keep_ranges))]
        parts.append(
            f"[0:a:{audio_index}]asplit={len(keep_ranges)}"
            f"{''.join(f'[{label}]' for label in source_labels)}"
        )
        log_info(
            f"Inserted asplit={len(keep_ranges)} for multi-range audio trim from [0:a:{audio_index}]."
        )
        for idx, (start, end) in enumerate(keep_ranges):
            label = f"a{idx}"
            labels.append(f"[{label}]")
            parts.append(
                f"[{source_labels[idx]}]atrim=start={start:.6f}:end={end:.6f},"
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

    join_items = []
    if answers.get("join_input_items"):
        join_items = [
            {
                "path": answers["input_path"],
                "probe": answers.get("probe") or {},
                "format": answers.get("format") or {},
                "streams": (
                    list(answers.get("video_streams") or [])
                    + list(answers.get("audio_streams") or [])
                    + list(answers.get("subtitle_streams") or [])
                    + list(answers.get("attachment_streams") or [])
                    + list(answers.get("data_streams") or [])
                ),
                "video_streams": answers.get("video_streams") or [],
                "audio_streams": answers.get("audio_streams") or [],
                "data_streams": answers.get("data_streams") or [],
                "duration": stream_duration_seconds({}, answers.get("format")) or 0.0,
            },
            *list(answers.get("join_input_items") or []),
        ]
    separator_specs = []
    if separator_specs:
        cmd = separator_specs[0]["cmd"]
        answers["separator_jobs"] = [
            {
                "index": spec["index"],
                "segment": spec["segment"],
                "output_path": spec["output_path"],
            }
            for spec in separator_specs
        ]
        answers["output_path"] = separator_specs[0]["output_path"]
    else:
        answers.pop("separator_jobs", None)
        if join_items:
            output_path = build_output_path(answers)
            answers["output_path"] = output_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            copy_compatible, reasons = join_copy_compatibility(join_items)
            can_copy = (
                copy_compatible
                and str(answers.get("video_codec", "")).lower() == "copy"
                and str(answers.get("audio_codec", "")).lower() == "copy"
                and answers.get("audio_tracks") in (None, "all")
                and source_metadata_keep_enabled(answers)
                and source_chapters_keep_enabled(answers)
                and source_subtitles_keep_enabled(answers)
                and not video_filters_required(answers)
                and not answers.get("cut_keep_ranges")
                and not loudnorm_transform_enabled(answers)
            )
            print_join_summary(join_items, copy_compatible, reasons)
            if can_copy:
                cmd = build_join_copy_command(answers, join_items, output_path)
            else:
                if copy_compatible:
                    note("Join inputs are stream-copy compatible, but selected encode settings require re-encoding.")
                else:
                    note("Join inputs are not stream-copy compatible. Re-encoding is required.")
                cmd = build_join_encode_command(answers, join_items, output_path)
        else:
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

    audio_codec = normalize_audio_codec(
        config_value(config, "audio_codec"),
        default_audio_codec_for_ext(answers.get("output_ext", "")),
    )
    answers["audio_codec"] = audio_codec
    if audio_codec == "copy":
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
    if not source_subtitles_keep_enabled(answers):
        answers["subtitle_tracks"] = []
        return
    answers["subtitle_tracks"] = parse_selection_config(
        config_value(config, "subtitle_tracks"),
        len(answers["subtitle_streams"]),
        [0],
        allow_none=True,
    )


def apply_config_source_extra_options(answers: dict[str, Any], config: dict[str, Any]) -> None:
    if not output_has_video(answers):
        return
    keep = parse_bool_config(config_value(config, "keep_source_metadata", "y"), True)
    answers["keep_source_metadata"] = keep
    answers["keep_source_chapters"] = keep
    answers["keep_source_subtitles"] = keep
    answers["keep_source_data_streams"] = keep
    answers["keep_source_extra_video_streams"] = keep
    if not keep:
        answers["subtitle_tracks"] = []
        answers["keep_embedded_attachments"] = False
        return
    keep_attachments = parse_bool_config(config_value(config, "keep_embedded_attachments", "n"), False)
    answers["keep_embedded_attachments"] = bool(
        keep_attachments and embedded_attachment_streams(answers) and output_supports_embedded_attachments(answers)
    )


def apply_unified_video_editor_answers(answers: dict[str, Any]) -> None:
    if not answers.get("_unified_video_editor_used"):
        return
    if "_unified_video_speed" in answers or "_unified_reverse_video" in answers:
        speed = clamp_speed_factor(answers.get("_unified_video_speed", DEFAULT_SPEED_FACTOR))
        reverse = bool(answers.get("_unified_reverse_video"))
        answers["video_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
        answers["video_speed_factor"] = speed
        answers["reverse_video"] = reverse
        answers["audio_speed_from_video"] = bool(answers.get("_unified_include_audio"))
    if "_unified_cut_keep_ranges" in answers:
        duration = stream_duration_seconds({}, answers.get("format")) or 0.0
        duration += sum(float(item.get("duration") or 0.0) for item in answers.get("join_input_items") or [])
        answers["cut_keep_ranges"] = normalize_cut_ranges(answers.get("_unified_cut_keep_ranges") or [], duration)


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
    apply_config_source_extra_options(answers, config)
    apply_config_subtitle_options(answers, config)


def run_wizard(answers: dict[str, Any]) -> None:
    steps = [
        Step("input_path", lambda a: True, step_input_path),
        Step("join_inputs", output_has_video, step_join_additional_inputs_for_encode),
        Step("output_location", lambda a: True, step_output_location),
        Step("output_format", lambda a: True, step_output_format),
        Step("video_codec", output_has_video, step_video_codec),
        Step("use_gpu", output_has_video, step_use_gpu),
        Step("unified_video_editor", output_has_video, step_unified_video_editor_for_encode),
        Step("crop_enabled", lambda a: output_has_video(a) and not a.get("_unified_video_editor_declined"), step_crop_enabled),
        Step("crop_top", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_top),
        Step("crop_left", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_left),
        Step("crop_right", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_right),
        Step("crop_bottom", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_bottom),
        Step("video_bitrate", video_reencode_options_applicable, step_video_bitrate),
        Step("nvenc_multipass", nvenc_multipass_prompt_applicable, step_nvenc_multipass),
        Step("cpu_two_pass", cpu_two_pass_applicable, step_cpu_two_pass),
        Step("resolution", video_reencode_options_applicable, step_resolution),
        Step("fps", video_reencode_options_applicable, step_fps),
        Step("video_speed_reverse", lambda a: output_has_video(a) and not a.get("_unified_video_editor_used") and not a.get("_unified_video_editor_declined"), step_video_speed_reverse_for_encode),
        Step("cuts", lambda a: video_reencode_options_applicable(a) and not a.get("_unified_video_editor_used") and not a.get("_unified_video_editor_declined"), step_cuts),
        Step("audio_tracks", lambda a: bool(a.get("audio_streams")), step_audio_tracks),
        Step("loudnorm", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_loudnorm),
        Step("audio_cut", audio_only_transform_prompt_applicable, step_audio_cut_for_encode),
        Step("audio_speed_reverse", audio_only_transform_prompt_applicable, step_audio_speed_reverse_for_encode),
        Step("audio_codec", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_audio_codec),
        Step("audio_bitrate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy" and audio_codec_uses_bitrate(str(a.get("audio_codec") or default_audio_codec_for_ext(a.get("output_ext", "")))), step_audio_bitrate),
        Step("source_extras", source_extra_policy_applicable, step_source_extra_policy),
        Step("subtitle_tracks", lambda a: output_has_video(a) and source_subtitles_keep_enabled(a) and bool(a.get("subtitle_streams")), step_subtitle_tracks),
        Step("start_now", lambda a: True, step_start_now),
    ]

    def next_index(start: int) -> int:
        idx = start
        while idx < len(steps) and not steps[idx].applicable(answers):
            idx += 1
        return idx

    def prev_index(start: int) -> int:
        idx = start
        while idx > 0 and (
            not steps[idx].applicable(answers)
            or is_auto_unified_crop_step(idx)
            or step_is_auto_back_skip(steps[idx], answers)
        ):
            idx -= 1
        return max(0, idx)

    def is_auto_unified_crop_step(pos: int) -> bool:
        return bool(
            answers.get("_unified_video_editor_used")
            and steps[pos].name in {"crop_enabled", "crop_top", "crop_left", "crop_right", "crop_bottom"}
        )

    def question_number(current: int) -> int:
        count = 0
        join_pos = next((pos for pos, step in enumerate(steps) if step.name == "join_inputs"), -1)
        for pos in range(current + 1):
            if steps[pos].applicable(answers) and not is_auto_unified_crop_step(pos):
                count += 1
        extra = int(answers.get("_join_question_extra", 0) or 0) if join_pos >= 0 and current >= join_pos else 0
        return answers.get("_question_offset", 0) + count + extra

    idx = next_index(0)
    while idx < len(steps):
        try:
            answers["_question_number"] = question_number(idx)
            steps[idx].run(answers)
            apply_unified_video_editor_answers(answers)
            idx = next_index(idx + 1)
        except Back:
            if idx == 0:
                raise
            idx = prev_index(idx - 1)


def log_final_normalized_answers(answers: dict[str, Any], cmd: list[str]) -> None:
    try:
        input_paths = [str(answers.get("input_path"))]
        input_paths.extend(str(item.get("path")) for item in answers.get("join_input_items") or [] if item.get("path"))
        source_duration = stream_duration_seconds({}, answers.get("format")) or 0.0
        split_points = normalize_separator_points(answers.get("separator_points"), final_processed_duration_for_splits(answers, source_duration))
        split_intervals = separator_ranges(split_points, final_processed_duration_for_splits(answers, source_duration)) if split_points else []
        text = " ".join(str(part) for part in cmd)
        summary = {
            "input_paths": input_paths,
            "output_path": str(answers.get("output_path")),
            "split_output_paths": [str(path) for path in answers.get("split_output_paths") or []],
            "video_codec": answers.get("video_codec"),
            "source_bit_depth": describe_video_bit_depth(source_video_stream(answers) or {}),
            "output_bit_depth": output_video_bit_depth(answers) if output_has_video(answers) else None,
            "output_cpu_pixel_format": cpu_pixel_format_for_output(answers) if output_has_video(answers) else None,
            "output_cuda_pixel_format": cuda_pixel_format_for_output(answers) if output_has_video(answers) else None,
            "audio_codec": answers.get("audio_codec"),
            "selected_audio_tracks": selected_audio_streams(answers) if answers.get("audio_streams") else [],
            "additional_video_streams": len(additional_source_video_streams(answers)),
            "attachment_streams": len(embedded_attachment_streams(answers)),
            "data_streams": len(source_data_streams(answers)),
            "keep_source_metadata": source_metadata_keep_enabled(answers),
            "keep_source_chapters": source_chapters_keep_enabled(answers),
            "keep_source_subtitles": source_subtitles_keep_enabled(answers),
            "keep_source_data_streams": source_data_keep_enabled(answers),
            "keep_source_extra_video_streams": source_extra_video_keep_enabled(answers),
            "keep_embedded_attachments": bool(answers.get("keep_embedded_attachments")),
            "crop_enabled": bool(answers.get("crop_enabled")),
            "crop_margins": {
                "top": answers.get("crop_top", 0),
                "left": answers.get("crop_left", 0),
                "right": answers.get("crop_right", 0),
                "bottom": answers.get("crop_bottom", 0),
            },
            "crop_box_dimensions": answers.get("crop_box_dimensions"),
            "final_resolution": answers.get("final_resolution"),
            "fps": answers.get("fps"),
            "video_speed": encode_video_speed_factor(answers) if video_speed_transform_enabled(answers) else 1.0,
            "audio_speed": encode_audio_speed_factor(answers) if audio_speed_transform_enabled(answers) else 1.0,
            "reverse_video": bool(answers.get("reverse_video")),
            "reverse_audio": encode_audio_reverse_enabled(answers),
            "split_enabled": bool(split_points),
            "split_points": split_points,
            "split_intervals": split_intervals,
            "loudnorm_enabled": loudnorm_transform_enabled(answers),
            "loudnorm_target_i": answers.get("loudnorm_target_i"),
            "loudnorm_measured": answers.get("loudnorm_measured"),
            "loudnorm_applied_tracks": selected_audio_streams(answers) if loudnorm_transform_enabled(answers) and answers.get("audio_streams") else [],
            "gpu_requested": bool(answers.get("use_gpu")),
            "cuda_fast_path": "-hwaccel_output_format cuda" in text and "scale_cuda" in text,
            "complex_cpu_graph": "-filter_complex" in cmd,
            "gpu_decode_only": "-hwaccel cuda" in text and "-hwaccel_output_format cuda" not in text,
            "nvenc_encode": "_nvenc" in text,
            "nvenc_multipass": normalize_nvenc_multipass_mode(answers.get("nvenc_multipass")),
            "nvenc_multipass_skip_reason": answers.get("nvenc_multipass_skip_reason"),
            "scale_cuda_used": "scale_cuda" in text,
            "hwdownload_used": "hwdownload" in text,
            "hwupload_cuda_used": "hwupload_cuda" in text,
        }
        log_info("Final normalized answers before execution:\n" + json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        log_warn(f"Could not log final normalized answers: {exc}")


def print_summary(answers: dict[str, Any], cmd: list[str]) -> None:
    two_pass_display = cpu_two_pass_enabled_for_command(answers, cmd)
    if two_pass_display:
        pass1_cmd, pass2_cmd, _passlog = build_cpu_two_pass_commands(cmd, answers)
        log_info("Final PowerShell command (CPU two-pass pass 1/2): " + command_to_powershell(pass1_cmd))
        log_info("Final PowerShell command (CPU two-pass pass 2/2): " + command_to_powershell(pass2_cmd))
        log_command("Actual final subprocess pass 1/2", pass1_cmd)
        log_command("Actual final subprocess pass 2/2", pass2_cmd)
    else:
        log_info("Final PowerShell command: " + command_to_powershell(cmd))
        log_command("Actual final subprocess", cmd)
    log_final_normalized_answers(answers, cmd)
    log_info(
        "Selected settings: input={}; output={}; format={}; video_codec={}; audio_codec={}; crop={}; fps={}; resolution={}".format(
            answers.get("input_path"), answers.get("output_path"), answers.get("output_ext"),
            answers.get("video_codec"), answers.get("audio_codec"),
            format_crop_margins(answers) if answers.get("crop_enabled") else "no",
            answers.get("fps") or "source", format_resolution_summary(answers.get("resolution")),
        )
    )
    print()
    if two_pass_display:
        pass1_cmd, pass2_cmd, _passlog = build_cpu_two_pass_commands(cmd, answers)
        print(paint("Final PowerShell command (CPU two-pass pass 1/2):", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        print(paint(command_to_powershell(pass1_cmd), Color.FINAL_COMMAND_TEXT))
        print()
        print(paint("Final PowerShell command (CPU two-pass pass 2/2):", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        print(paint(command_to_powershell(pass2_cmd), Color.FINAL_COMMAND_TEXT))
    else:
        print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    print()
    print(paint("Selected settings summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("input", answers["input_path"], Color.WHITE))
    if answers.get("join_input_items"):
        join_items = list(answers.get("join_input_items") or [])
        print("  " + field_text("joined inputs", len(join_items) + 1, Color.LIGHT_BLUE))
        print("    " + paint(f"1. {Path(answers['input_path']).name}", Color.WHITE))
        for idx, item in enumerate(join_items, start=2):
            print("    " + paint(f"{idx}. {Path(item.get('path')).name}", Color.WHITE))
        print("  " + field_text("join settings", "video/audio settings apply by track number to every joined input", Color.YELLOW))
    print("  " + field_text("output", answers["output_path"], Color.LIME))
    if output_has_video(answers):
        print("  " + field_text("video codec", answers.get("video_codec", DEFAULT_VIDEO_CODEC), Color.CYAN))
        print("  " + field_text("source bit depth", describe_video_bit_depth(source_video_stream(answers) or {}), Color.PINK))
        print("  " + field_text("output bit depth", f"{output_video_bit_depth(answers)}-bit", Color.PINK))
        print("  " + field_text("GPU", "yes" if answers.get("use_gpu") else "no", Color.GREEN if answers.get("use_gpu") else Color.YELLOW))
        if "_nvenc" in command_to_text(cmd):
            print("  " + field_text("NVENC multipass", normalize_nvenc_multipass_mode(answers.get("nvenc_multipass")), Color.YELLOW))
        if answers.get("crop_enabled"):
            print("  " + field_text("crop", "yes, " + format_crop_margins(answers), Color.ORANGE))
            crop_box = answers.get("crop_box_dimensions")
            crop_ar = answers.get("cropped_aspect_ratio")
            if crop_box and crop_ar:
                print("  " + field_text("crop box", f"{crop_box[0]}x{crop_box[1]} (AR {crop_ar:.4f})", Color.ORANGE))
        else:
            print("  " + field_text("crop", "no", Color.GREEN))
        print("  " + field_text("video bitrate", str(answers.get("video_bitrate_kbps") or "source/default") + " kbps", Color.YELLOW))
        if answers.get("cpu_two_pass"):
            print("  " + field_text("CPU two-pass", "yes", Color.YELLOW))
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
        if answers.get("separator_points"):
            print(paint(format_split_points_for_summary(answers.get("separator_points") or [], get_video_fps(answers), "Split points"), Color.LIGHT_BLUE))
        if answers.get("split_output_paths"):
            print("  " + field_text("Split output parts", len(answers.get("split_output_paths") or []), Color.LIGHT_BLUE))
            split_intervals = list(answers.get("split_part_intervals") or [])
            for idx, part_path in enumerate(answers.get("split_output_paths") or [], start=1):
                interval_text = ""
                if idx - 1 < len(split_intervals):
                    start, end = split_intervals[idx - 1]
                    duration = max(0.0, end - start)
                    interval_text = f"  [{seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}, duration {format_elapsed(duration)}]"
                print("    " + field_text(f"Part {idx:02d}", str(part_path) + interval_text, Color.LIME))
    if answers.get("audio_streams"):
        print("  " + field_text("audio tracks", answers.get("audio_tracks"), Color.LIGHT_BLUE))
        print("  " + field_text("audio codec", answers.get("audio_codec"), Color.CYAN))
        print("  " + field_text("audio bitrate", str(answers.get("audio_bitrate_kbps") or "source/default") + " kbps", Color.YELLOW))
        if audio_cut_transform_enabled(answers):
            print(paint(format_audio_ranges_for_summary(answers["audio_cut_keep_ranges"], "audio cuts (keep ranges)"), Color.LIME))
        if audio_speed_transform_enabled(answers):
            print("  " + field_text("audio speed", f"{encode_audio_speed_factor(answers) * 100:.0f}%", Color.MAGENTA))
            print("  " + field_text("reverse audio", "yes" if encode_audio_reverse_enabled(answers) else "no", Color.ORANGE))
        if loudnorm_transform_enabled(answers):
            mode = "two-pass" if answers.get("loudnorm_measured") else "single-pass"
            print("  " + field_text("LoudNorm", f"I={answers.get('loudnorm_target_i', LOUDNORM_DEFAULT_TARGET_I):g} LUFS ({mode})", Color.MEAN_VOLUME))
    if output_has_video(answers) and answers.get("subtitle_streams"):
        if source_subtitles_keep_enabled(answers):
            print("  " + field_text("subtitle tracks", answers.get("subtitle_tracks"), Color.WHITE))
        else:
            print("  " + field_text("subtitle tracks", "removed by metadata policy", Color.ORANGE))
    if output_has_video(answers) and source_extra_preservation_features(answers):
        print("  " + field_text("source metadata", "keep" if source_metadata_keep_enabled(answers) else "remove", Color.LIGHT_BLUE))
        print("  " + field_text("chapters", "keep" if source_chapters_keep_enabled(answers) else "remove", Color.LIGHT_BLUE))
    if output_has_video(answers) and embedded_attachment_streams(answers):
        attachment_state = "yes" if embedded_attachment_keep_enabled(answers) else "no"
        print("  " + field_text("embedded attachments", attachment_state, Color.PINK))
    cut_keep_ranges = answers.get("cut_keep_ranges") or []
    if cut_keep_ranges:
        fps = get_video_fps(answers)
        print(paint(
            format_cut_ranges_for_summary(cut_keep_ranges, fps, "cuts (keep ranges)"),
            Color.LIME,
        ))


def cpu_two_pass_enabled_for_command(answers: dict[str, Any], cmd: list[str]) -> bool:
    if not answers.get("cpu_two_pass"):
        return False
    text = " ".join(str(part) for part in cmd)
    return ("-c:v libx264" in text or "-c:v:0 libx264" in text or "-c:v libx265" in text or "-c:v:0 libx265" in text)


def cpu_two_pass_log_prefix(answers: dict[str, Any]) -> Path:
    existing = answers.get("_cpu_two_pass_passlogfile")
    if existing:
        return Path(str(existing))
    safe_stem = sanitize_output_stem(Path(str(answers.get("output_path") or "ffmwiz")).stem)[:48] or "ffmwiz"
    passlog = Path(tempfile.gettempdir()) / f"ffmwiz_2pass_{safe_stem}_{os.getpid()}_{int(time.time())}"
    answers["_cpu_two_pass_passlogfile"] = str(passlog)
    return passlog


def ffmpeg_input_section_end(cmd: list[str]) -> int:
    idx = 0
    end = 1
    while idx < len(cmd):
        if cmd[idx] == "-i" and idx + 1 < len(cmd):
            end = idx + 2
            idx += 2
            continue
        idx += 1
    return end


def cpu_two_pass_video_output_args(output_args: list[str]) -> list[str]:
    prefixes_with_values = (
        "-filter:v",
        "-vf",
        "-r:v",
        "-fps_mode:v",
        "-c:v",
        "-preset",
        "-profile:v",
        "-b:v",
        "-maxrate:v",
        "-bufsize:v",
        "-color_range:v",
        "-pix_fmt",
        "-x264-params",
        "-x265-params",
    )
    result: list[str] = []
    idx = 0
    while idx < len(output_args):
        opt = output_args[idx]
        if any(opt == prefix or opt.startswith(prefix + ":") for prefix in prefixes_with_values):
            if idx + 1 >= len(output_args):
                raise ValueError(f"Missing value for two-pass video option: {opt}")
            if opt != "-c" and output_args[idx + 1] != "copy":
                result.extend([opt, output_args[idx + 1]])
            idx += 2
            continue
        idx += 1
    text = " ".join(result)
    if "-c:v" not in text:
        raise ValueError("CPU two-pass command could not find the final video encoder options.")
    return result


def build_cpu_two_pass_commands(cmd: list[str], answers: dict[str, Any]) -> tuple[list[str], list[str], Path]:
    if len(cmd) < 2:
        raise ValueError("FFmpeg command is too short for two-pass encoding.")
    passlog = cpu_two_pass_log_prefix(answers)
    input_end = ffmpeg_input_section_end(cmd)
    input_args = list(cmd[:input_end])
    output_args = list(cmd[input_end:-1])
    video_args = cpu_two_pass_video_output_args(output_args)
    first = (
        input_args
        + ["-map", "0:v:0"]
        + video_args
        + ["-pass", "1", "-passlogfile", str(passlog), "-an", "-sn", "-dn", "-f", "null", os.devnull]
    )
    second = list(cmd[:-1]) + ["-pass", "2", "-passlogfile", str(passlog), str(cmd[-1])]
    return first, second, passlog


def cleanup_cpu_two_pass_logs(passlog: Path) -> None:
    parent = passlog.parent
    prefix = passlog.name
    try:
        for path in parent.glob(prefix + "*"):
            try:
                path.unlink()
            except OSError:
                pass
    except OSError:
        pass


def run_cpu_two_pass_ffmpeg(
    cmd: list[str],
    answers: dict[str, Any],
    *,
    total_duration: float | None,
    progress_output_paths: list[Path],
) -> tuple[int, float]:
    first, second, passlog = build_cpu_two_pass_commands(cmd, answers)
    log_info("CPU two-pass encoding enabled.")
    log_command("CPU two-pass pass 1", first)
    log_command("CPU two-pass pass 2", second)
    try:
        print(paint("Starting FFmpeg pass 1/2...", Color.GREEN))
        rc1, elapsed1 = run_ffmpeg_with_progress(
            first,
            total_duration=total_duration,
            label="FFmpeg encode pass 1/2",
            initial_detail="CPU two-pass analysis pass",
        )
        if rc1 != 0:
            return rc1, elapsed1
        print()
        print(paint("Starting FFmpeg pass 2/2...", Color.GREEN))
        rc2, elapsed2 = run_ffmpeg_with_progress(
            second,
            total_duration=total_duration,
            label="FFmpeg encode pass 2/2",
            initial_detail="CPU two-pass final encode pass",
            progress_output_paths=progress_output_paths,
        )
        return rc2, elapsed1 + elapsed2
    finally:
        cleanup_cpu_two_pass_logs(passlog)


def graphical_hint(text: str) -> str:
    if USE_COLOR:
        return f"{Color.AQUA}{text}{Color.RESET}{Color.HINT_YELLOW}"
    return text


def colored_unified_editor_hint() -> str:
    return paint("(combines crop, cuts, speed/reverse, and audio waveform preview)", Color.HINT_YELLOW)


def run_mode_steps(answers: dict[str, Any], steps: list[Step]) -> None:
    def visible_question_number(current: int) -> int:
        count = 0
        for pos in range(current + 1):
            if steps[pos].applicable(answers):
                count += 1
        return int(answers.get("_question_offset", 0) or 0) + count

    idx = 0
    while idx < len(steps):
        if not steps[idx].applicable(answers):
            idx += 1
            continue
        try:
            answers["_question_number"] = visible_question_number(idx)
            steps[idx].run(answers)
            idx += 1
        except Back:
            if idx == 0:
                raise
            idx -= 1
            while idx > 0 and (not steps[idx].applicable(answers) or step_is_auto_back_skip(steps[idx], answers)):
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
        if is_back_value(value):
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
                if is_back_value(speed_text):
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
    allow_gui = not answers.get("_disable_graphical_editors") and not answers.get("_disable_followup_video_gui_prompts")
    while True:
        hint = (
            f"y/n, {graphical_hint('g=Show Graphical Video Speed Editor')}; "
            "speed/reverse requires video re-encoding"
            if allow_gui
            else "y/n; speed/reverse requires video re-encoding"
        )
        value = ask_raw(question_prompt(answers, "Change video speed or reverse video?", hint, "n"))
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["video_speed_enabled"] = False
            return
        if lowered in {"g", "gui", "graphical"}:
            if not allow_gui:
                error("Graphical video speed editor is not available here. Use the Unified Video Editor or manual settings.")
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
                if is_back_value(speed_text):
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
        if is_back_value(value):
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
                if is_back_value(speed_text):
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
        if is_back_value(value):
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
                if is_back_value(speed_text):
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
    volume_stats = get_audio_volume_stats(answers)
    for idx, stream in enumerate(streams):
        print(
            f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
            f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
            f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
            f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
            f"{field_text('bitrate', describe_bitrate(stream_bitrate_kbps(stream, fmt, packet_sizes)), Color.YELLOW)} | "
            f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, idx), Color.MEAN_VOLUME)}"
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
        if is_back_value(value):
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
        if is_back_value(value):
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


def print_ffmpeg_processing_plan(
    answers: dict[str, Any],
    cmd: list[str],
    total_duration: float | None,
    processed_duration: float | None = None,
) -> None:
    text = " ".join(str(part) for part in cmd)
    has_nvdec = "-hwaccel cuda" in text
    has_cuda_frames = "-hwaccel_output_format cuda" in text
    has_nvenc = "_nvenc" in text
    has_complex = "-filter_complex" in cmd
    if has_cuda_frames:
        path = "CUDA fast path: NVDEC/CUDA decode -> CUDA filters -> NVENC encode"
    elif has_nvdec and has_nvenc and has_complex:
        path = "Hybrid GPU path: CUDA/NVDEC decode -> CPU filter graph -> NVENC encode"
    elif has_nvenc:
        path = "NVENC encode path: CPU decode/filter -> NVENC encode"
    elif has_complex:
        path = "CPU filter graph path"
    else:
        path = "Standard FFmpeg path"
    print("  " + field_text("processing path", path, Color.LIGHT_BLUE))
    log_info(f"FFmpeg processing path: {path}")
    if has_cuda_frames:
        print("  " + field_text("decode", "CUDA frames are kept on GPU; scale_cuda is used where scaling is needed", Color.CYAN))
        log_info("Command decision: CUDA fast path enabled; hwaccel_output_format cuda is intentional.")
    elif has_nvdec and has_nvenc and has_complex:
        print("  " + field_text("decode", "CUDA/NVDEC is used before each video input", Color.CYAN))
        print("  " + field_text("filters", "CPU filter_complex: crop/fps/scale/pad/concat/trim/speed/Split/audio filters", Color.ORANGE))
        print("  " + field_text("encode", "NVENC is used for final video encoding", Color.LIME))
        log_info("Command decision: complex graph uses CPU filters; CUDA decode-only enabled; hwaccel_output_format cuda intentionally omitted.")
        log_info("Command decision: scale_cuda/pad_cuda/hwdownload/hwupload_cuda intentionally not used in complex CPU graph.")
    elif has_nvenc:
        print("  " + field_text("encode", "NVENC is used for final video encoding", Color.LIME))
        log_info("Command decision: NVENC encode path without CUDA frame filtering.")
    elif has_complex:
        print("  " + field_text("filters", "CPU filter_complex", Color.ORANGE))
        log_info("Command decision: CPU filter graph path.")
    if answers.get("join_input_items"):
        print("  " + field_text("join", f"{len(answers.get('join_input_items') or []) + 1} input videos", Color.CYAN))
    if answers.get("cut_keep_ranges"):
        print("  " + field_text("cuts", f"{len(answers.get('cut_keep_ranges') or [])} keep range(s)", Color.ORANGE))
    if answers.get("separator_points"):
        print("  " + field_text("Split", f"{len(answers.get('separator_points') or []) + 1} output part(s)", Color.LIGHT_BLUE))
        if processed_duration and processed_duration > 0:
            print("  " + field_text("processed duration", format_elapsed(processed_duration), Color.CYAN))
            print("  " + field_text("progress basis", "aggregate Split timeline from encoded frames", Color.GRAY))
    if loudnorm_transform_enabled(answers):
        print("  " + field_text("LoudNorm", f"I={answers.get('loudnorm_target_i', LOUDNORM_DEFAULT_TARGET_I):g} LUFS", Color.MEAN_VOLUME))
    if total_duration and total_duration > 0:
        print("  " + field_text("progress duration", format_elapsed(total_duration), Color.MAGENTA))
    if has_complex:
        print("  " + field_text("startup phase", "decoding input, priming filter_complex, then feeding encoder", Color.GRAY))


def ffmpeg_initial_progress_detail(answers: dict[str, Any], cmd: list[str]) -> str:
    text = " ".join(str(part) for part in cmd)
    has_nvdec = "-hwaccel cuda" in text
    has_cuda_frames = "-hwaccel_output_format cuda" in text
    has_nvenc = "_nvenc" in text
    has_complex = "-filter_complex" in cmd
    if has_cuda_frames:
        return "launching CUDA decode/filter path and waiting for first encoded timestamp"
    if has_nvdec and has_nvenc and has_complex:
        return "CUDA/NVDEC decoding -> CPU filter_complex -> NVENC encoding; waiting for first encoded timestamp"
    if has_nvenc and has_complex:
        return "CPU decode/filter_complex -> NVENC encoding; waiting for first encoded timestamp"
    if has_complex:
        return "CPU filter_complex is starting; waiting for first encoded timestamp"
    return "starting FFmpeg and waiting for first progress timestamp"


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


METADATA_DISPOSITION_FLAGS = (
    "default",
    "forced",
    "hearing_impaired",
    "visual_impaired",
    "commentary",
    "original",
    "karaoke",
    "lyrics",
    "attached_pic",
)


def metadata_prompt(answers: dict[str, Any], title: str, details: str | None = None,
                    default: str | None = None, back: str = "back=0, quit=exit") -> str:
    answers["_metadata_question_number"] = int(answers.get("_metadata_question_number", 0)) + 1
    saved = answers.get("_question_number")
    answers["_question_number"] = answers["_metadata_question_number"]
    try:
        return question_prompt(answers, title, details, default, back)
    finally:
        if saved is None:
            answers.pop("_question_number", None)
        else:
            answers["_question_number"] = saved


def metadata_menu_item(number: int, label: str, default: bool = False) -> str:
    marker = f" {paint('[' + str(number) + ']', Color.GREEN)}" if default else ""
    return f"  {paint(str(number) + '.', Color.LIGHT_BLUE)} {label}{marker}"


def metadata_menu_selection(default: str = "1", back: str = "0=back, quit=exit") -> str:
    value = ask_raw(f"{paint('Selection', Color.BOLD)} {paint('[' + default + ']', Color.GREEN)} {back_text(back)}: ")
    return default if value == "" else value


def probe_media_json(input_path: Path, ffprobe: str | None = None) -> dict[str, Any]:
    ffprobe_bin = ffprobe or shutil.which("ffprobe") or "ffprobe"
    args = [
        ffprobe_bin,
        "-hide_banner",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-of",
        "json",
        str(input_path),
    ]
    log_info("Metadata Editor ffprobe command: " + command_to_powershell(args))
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        log_error("Metadata Editor ffprobe failed:\n" + (result.stderr or result.stdout or "").strip())
        raise FFprobeError(f"ffprobe could not read this file. See log file: {_log_file_text()}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        log_error("Metadata Editor ffprobe returned invalid JSON:\n" + _text_preview(result.stdout, 3000))
        raise FFprobeError(f"ffprobe returned invalid JSON. See log file: {_log_file_text()}") from exc
    if not isinstance(payload, dict):
        raise FFprobeError("ffprobe returned an unexpected JSON payload.")
    return payload


def metadata_stream_index(stream: dict[str, Any]) -> int | None:
    try:
        return int(stream.get("index"))
    except (TypeError, ValueError):
        return None


def metadata_stream_type(stream: dict[str, Any]) -> str:
    return str(stream.get("codec_type") or "unknown").lower()


def metadata_tags(stream: dict[str, Any]) -> dict[str, Any]:
    return stream.get("tags") if isinstance(stream.get("tags"), dict) else {}


def metadata_disposition_summary(stream: dict[str, Any]) -> str:
    disposition = stream.get("disposition") if isinstance(stream.get("disposition"), dict) else {}
    flags = [name for name in METADATA_DISPOSITION_FLAGS if int(disposition.get(name) or 0)]
    return ",".join(flags) if flags else "-"


def metadata_stream_spec(probe_json: dict[str, Any], stream: dict[str, Any]) -> str:
    stream_index = metadata_stream_index(stream)
    codec_type = metadata_stream_type(stream)
    prefix = {"video": "v", "audio": "a", "subtitle": "s", "attachment": "t", "data": "d"}.get(codec_type)
    if prefix is None:
        return str(stream_index if stream_index is not None else 0)
    relative = 0
    for candidate in probe_json.get("streams") or []:
        if metadata_stream_type(candidate) != codec_type:
            continue
        if metadata_stream_index(candidate) == stream_index:
            return f"{prefix}:{relative}"
        relative += 1
    return str(stream_index if stream_index is not None else 0)


def metadata_stream_relative_index(probe_json: dict[str, Any], stream: dict[str, Any]) -> int:
    spec = metadata_stream_spec(probe_json, stream)
    try:
        return int(spec.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return 0


def metadata_type_color(codec_type: str) -> str:
    return {
        "video": Color.MAGENTA,
        "audio": Color.BLUE,
        "subtitle": Color.LIGHT_YELLOW,
        "attachment": Color.MUX_LAVENDER,
        "data": Color.GRAY,
    }.get(codec_type, Color.WHITE)


def metadata_stream_line(probe_json: dict[str, Any], stream: dict[str, Any]) -> str:
    codec_type = metadata_stream_type(stream)
    tags = metadata_tags(stream)
    parts = [
        field_text("stream index", metadata_stream_index(stream), Color.LIGHT_BLUE),
        field_text("type", codec_type, metadata_type_color(codec_type)),
        field_text("codec", stream.get("codec_name") or "unknown", Color.CYAN),
    ]
    if codec_type != "video":
        parts.extend([
            field_text("language", display_language(tags.get("language")), Color.GREEN),
            field_text("title", tags.get("title") or "unknown", Color.WHITE),
        ])
    parts.extend([
        field_text("disposition", metadata_disposition_summary(stream), Color.YELLOW),
        field_text("spec", metadata_stream_spec(probe_json, stream), Color.MAGENTA),
    ])
    if codec_type == "video":
        parts.extend([
            field_text("size", f"{stream.get('width', '?')}x{stream.get('height', '?')}", Color.LIME),
            field_text("pix_fmt", stream.get("pix_fmt") or "unknown", Color.ORANGE),
            field_text("color_range", stream.get("color_range") or "unknown", Color.COLOR_RANGE_VALUE),
            field_text("color_space", stream.get("color_space") or "unknown", Color.LIGHT_BLUE),
            field_text("color_transfer", stream.get("color_transfer") or "unknown", Color.PINK),
            field_text("color_primaries", stream.get("color_primaries") or "unknown", Color.AQUA),
        ])
    elif codec_type == "audio":
        parts.extend([
            field_text("sample_rate", stream.get("sample_rate") or "unknown", Color.MAGENTA),
            field_text("channels", stream.get("channels") or "unknown", Color.GREEN),
            field_text("channel_layout", stream.get("channel_layout") or "unknown", Color.AQUA),
        ])
    elif codec_type == "subtitle":
        parts.append(field_text("subtitle codec", stream.get("codec_name") or "unknown", Color.LIGHT_YELLOW))
    return " | ".join(str(part) for part in parts)


def list_streams_for_selection(probe_json: dict[str, Any], streams: list[dict[str, Any]] | None = None) -> None:
    print()
    print(paint("Streams", Color.BOLD + Color.LIGHT_BLUE))
    for stream in (streams if streams is not None else (probe_json.get("streams") or [])):
        print("  " + metadata_stream_line(probe_json, stream))


def select_stream(probe_json: dict[str, Any], answers: dict[str, Any],
                  allowed_types: set[str] | None = None) -> dict[str, Any]:
    allowed_types = {item.lower() for item in allowed_types} if allowed_types else None
    streams = [
        stream for stream in (probe_json.get("streams") or [])
        if allowed_types is None or metadata_stream_type(stream) in allowed_types
    ]
    if not streams:
        error("No matching streams were found for this operation.")
        raise Back()
    list_streams_for_selection(probe_json, streams)
    by_index = {metadata_stream_index(stream): stream for stream in streams if metadata_stream_index(stream) is not None}
    while True:
        value = ask_raw(metadata_prompt(
            answers,
            "Enter stream index",
            "use the real ffprobe stream index shown above; use b to go back",
            back="back=b, quit=exit",
        ))
        if str(value).strip().lower() in {"b", "back"}:
            raise Back()
        if not re.fullmatch(r"\d+", value or ""):
            error("Enter a numeric stream index from the list above.")
            continue
        stream = by_index.get(int(value))
        if stream is None:
            error("That stream index is not available for this operation.")
            continue
        log_info(
            f"Metadata Editor selected stream: index={value}; "
            f"type={metadata_stream_type(stream)}; spec={metadata_stream_spec(probe_json, stream)}"
        )
        return stream


def metadata_output_path(input_path: Path, suffix: str, output_ext: str | None = None) -> Path:
    ext = output_ext or input_path.suffix or ".mkv"
    if ext and not str(ext).startswith("."):
        ext = "." + str(ext)
    candidate = input_path.with_name(f"{sanitize_output_stem(input_path.stem)}{suffix}{ext}")
    candidate = resolve_output_collision_against_inputs(candidate, [input_path], suffix or "_metadata")
    return unique_numbered_path(candidate)


def metadata_report_output_path(input_path: Path, suffix: str, ext: str) -> Path:
    report_dir = default_media_reports_dir()
    report_dir.mkdir(parents=True, exist_ok=True)
    candidate = report_dir / f"{sanitize_output_stem(input_path.name)}{suffix}{ext}"
    return unique_numbered_path(candidate)


def confirm_and_run_ffmpeg(answers: dict[str, Any], cmd: list[str], label: str,
                           output_path: Path | None = None) -> bool:
    print()
    print(paint("Final PowerShell command:", Color.FINAL_COMMAND_LABEL))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    log_info(f"{label} command: " + command_to_powershell(cmd))
    if not ask_yes_no(metadata_prompt(answers, "Start FFmpeg now?", "y/n", "y"), True):
        note("FFmpeg was not started. Returning to the Metadata Editor menu.")
        return False
    rc, _elapsed = run_ffmpeg_with_progress(cmd, total_duration=None, label=label)
    if rc == 0:
        if output_path is not None:
            note(f"Metadata operation finished: {output_path}")
        return True
    error(f"Metadata operation failed. Full output is in: {_log_file_text()}")
    return False


def metadata_stream_copy_command(ffmpeg: str, input_path: Path, output_path: Path) -> list[str]:
    return [ffmpeg, "-hide_banner", "-y", "-i", str(input_path), "-map", "0", "-c", "copy"]


def metadata_refresh_probe(answers: dict[str, Any]) -> dict[str, Any]:
    probe = probe_media_json(answers["metadata_input_path"], answers.get("ffprobe"))
    answers["metadata_probe"] = probe
    return probe


def metadata_show_input_overview(answers: dict[str, Any]) -> None:
    probe = metadata_refresh_probe(answers)
    input_path = answers["metadata_input_path"]
    print()
    print(paint("Metadata Editor input:", Color.BOLD + Color.LIGHT_BLUE))
    print("  " + field_text("Path", input_path, Color.WHITE))
    fmt = probe.get("format") if isinstance(probe.get("format"), dict) else {}
    print("  " + field_text("Container", fmt.get("format_name") or "unknown", Color.CYAN))
    print("  " + field_text("Duration", format_duration(stream_duration_seconds({}, fmt)), Color.MAGENTA))
    print("  " + field_text("Chapters", len(probe.get("chapters") or []), Color.YELLOW))
    list_streams_for_selection(probe)


def metadata_set_current_input(answers: dict[str, Any], output_path: Path) -> None:
    if output_path.exists():
        answers["metadata_input_path"] = output_path
        metadata_refresh_probe(answers)
        note(f"Metadata Editor current input is now: {output_path}")


def metadata_prompt_input(base_answers: dict[str, Any]) -> dict[str, Any]:
    answers = dict(base_answers)
    answers["_metadata_question_number"] = 0
    while True:
        value = ask_required(
            metadata_prompt(answers, "Enter input media file path", r"drag and drop a file here or paste a path; example: D:\Videos\input.mkv"),
            allow_n=False,
        )
        input_path = terminal_path(value)
        if not input_path.exists() or not input_path.is_file():
            error("Input file was not found.")
            continue
        answers["metadata_input_path"] = input_path
        metadata_show_input_overview(answers)
        return answers


def metadata_value_prompt(answers: dict[str, Any], title: str, allow_empty: bool = False) -> str:
    while True:
        value = ask_raw(metadata_prompt(answers, title, back="back=b, quit=exit"))
        if value.lower().strip() in {"b", "back"}:
            raise Back()
        if value or allow_empty:
            return value
        error("This value cannot be empty.")


def run_stream_metadata_editor(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Stream Metadata Editor", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Edit stream title", default=True))
        print(metadata_menu_item(2, "Edit stream language"))
        print(metadata_menu_item(3, "Remove stream title"))
        print(metadata_menu_item(4, "Remove stream language"))
        print(metadata_menu_item(5, "Custom stream metadata key/value"))
        print(metadata_menu_item(6, "Remove custom stream metadata key"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            return
        if choice not in {"1", "2", "3", "4", "5", "6"}:
            error("Enter a menu number from 1 to 6.")
            continue
        try:
            probe = metadata_refresh_probe(answers)
            stream = select_stream(probe, answers)
            spec = metadata_stream_spec(probe, stream)
            suffix = "_metadata_stream"
            if choice == "1":
                value = metadata_value_prompt(answers, "Enter new stream title")
                metadata_arg = f"title={value}"
                suffix = "_metadata_stream_title"
            elif choice == "2":
                note("Language examples: Japanese=jpn, English=eng, Persian=per or fas, Arabic=ara, Korean=kor, Chinese=chi or zho.")
                value = metadata_value_prompt(answers, "Enter ISO 639-2 language code")
                metadata_arg = f"language={value}"
                suffix = "_metadata_language"
            elif choice == "3":
                metadata_arg = "title="
                suffix = "_metadata_title_removed"
            elif choice == "4":
                metadata_arg = "language="
                suffix = "_metadata_language_removed"
            elif choice == "5":
                key = metadata_value_prompt(answers, "Enter metadata key")
                value = metadata_value_prompt(answers, "Enter metadata value")
                metadata_arg = f"{key}={value}"
                suffix = "_metadata_custom"
            else:
                key = metadata_value_prompt(answers, "Enter metadata key to remove")
                metadata_arg = f"{key}="
                suffix = "_metadata_custom_removed"
            output_path = metadata_output_path(answers["metadata_input_path"], suffix)
            cmd = metadata_stream_copy_command(answers["ffmpeg"], answers["metadata_input_path"], output_path)
            cmd.extend([f"-metadata:s:{spec}", metadata_arg, str(output_path)])
            if confirm_and_run_ffmpeg(answers, cmd, "Stream Metadata Editor", output_path):
                metadata_set_current_input(answers, output_path)
        except Back:
            continue


def metadata_dispositions_text(ffmpeg: str) -> str:
    args = [ffmpeg, "-hide_banner", "-dispositions"]
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
    return (result.stdout or result.stderr or "").strip()


def run_stream_disposition_editor(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Stream Disposition Editor", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Set audio stream as default", default=True))
        print(metadata_menu_item(2, "Remove default flag from audio stream"))
        print(metadata_menu_item(3, "Set subtitle stream as default"))
        print(metadata_menu_item(4, "Remove default flag from subtitle stream"))
        print(metadata_menu_item(5, "Set subtitle stream as forced"))
        print(metadata_menu_item(6, "Remove forced flag from subtitle stream"))
        print(metadata_menu_item(7, "Set commentary flag"))
        print(metadata_menu_item(8, "Remove commentary flag"))
        print(metadata_menu_item(9, "Set original flag"))
        print(metadata_menu_item(10, "Remove original flag"))
        print(metadata_menu_item(11, "Clear all disposition flags from selected stream"))
        print(metadata_menu_item(12, "Custom disposition flag editor"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            return
        if choice not in {str(i) for i in range(1, 13)}:
            error("Enter a menu number from 1 to 12.")
            continue
        try:
            if choice == "12":
                available = metadata_dispositions_text(answers["ffmpeg"])
                if available:
                    print()
                    print(paint("Available FFmpeg dispositions:", Color.BOLD + Color.LIGHT_BLUE))
                    print(available)
            probe = metadata_refresh_probe(answers)
            allowed = None
            flag = ""
            remove_flag = False
            clear_type_default = False
            if choice in {"1", "2"}:
                allowed = {"audio"}
                flag = "default"
                remove_flag = choice == "2"
            elif choice in {"3", "4", "5", "6"}:
                allowed = {"subtitle"}
                flag = "default" if choice in {"3", "4"} else "forced"
                remove_flag = choice in {"4", "6"}
            elif choice in {"7", "8"}:
                flag = "commentary"
                remove_flag = choice == "8"
            elif choice in {"9", "10"}:
                flag = "original"
                remove_flag = choice == "10"
            elif choice == "11":
                flag = "0"
            else:
                flag = metadata_value_prompt(answers, "Enter disposition flag name")
                remove_flag = ask_yes_no(metadata_prompt(answers, "Remove this flag instead of setting it?", "y/n", "n"), False)
            stream = select_stream(probe, answers, allowed)
            spec = metadata_stream_spec(probe, stream)
            codec_type = metadata_stream_type(stream)
            if flag == "default" and not remove_flag:
                note("Setting default on one stream may not automatically remove default from other streams.")
                clear_type_default = ask_yes_no(metadata_prompt(answers, "Make this stream the only default stream of its type?", "y/n", "n"), False)
            disposition_value = "0" if flag == "0" else (f"-{flag}" if remove_flag else flag)
            output_path = metadata_output_path(answers["metadata_input_path"], "_metadata_disposition")
            cmd = metadata_stream_copy_command(answers["ffmpeg"], answers["metadata_input_path"], output_path)
            if clear_type_default:
                prefix = {"audio": "a", "subtitle": "s", "video": "v"}.get(codec_type)
                if prefix:
                    cmd.extend([f"-disposition:{prefix}", "0"])
            cmd.extend([f"-disposition:{spec}", disposition_value, str(output_path)])
            if confirm_and_run_ffmpeg(answers, cmd, "Stream Disposition Editor", output_path):
                metadata_set_current_input(answers, output_path)
        except Back:
            continue


def metadata_chapter_lines(probe: dict[str, Any]) -> list[str]:
    chapters = probe.get("chapters") or []
    if not chapters:
        return ["No chapters were found."]
    lines = []
    for idx, chapter in enumerate(chapters, 1):
        tags = chapter.get("tags") if isinstance(chapter.get("tags"), dict) else {}
        title = tags.get("title") or "untitled"
        start = float(chapter.get("start_time") or 0.0)
        end = float(chapter.get("end_time") or 0.0)
        lines.append(f"{idx}. {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)} | {title}")
    return lines


def run_chapter_metadata_editor(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Chapter Metadata Editor", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Show chapters", default=True))
        print(metadata_menu_item(2, "Export metadata and chapters to ffmetadata file"))
        print(metadata_menu_item(3, "Import metadata and chapters from ffmetadata file"))
        print(metadata_menu_item(4, "Remove all chapters"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            return
        try:
            input_path = answers["metadata_input_path"]
            if choice == "1":
                for line in metadata_chapter_lines(metadata_refresh_probe(answers)):
                    print("  " + line)
            elif choice == "2":
                output_path = metadata_report_output_path(input_path, "_ffmetadata", ".txt")
                cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path), "-f", "ffmetadata", str(output_path)]
                confirm_and_run_ffmpeg(answers, cmd, "Chapter Metadata Export", output_path)
            elif choice == "3":
                note("Importing ffmetadata may replace metadata according to the file content.")
                metadata_file = terminal_path(ask_required(metadata_prompt(answers, "Enter ffmetadata file path")))
                if not metadata_file.exists() or not metadata_file.is_file():
                    error("Metadata file was not found.")
                    continue
                output_path = metadata_output_path(input_path, "_chapters_imported")
                cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path), "-i", str(metadata_file), "-map", "0", "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", str(output_path)]
                if confirm_and_run_ffmpeg(answers, cmd, "Chapter Metadata Import", output_path):
                    metadata_set_current_input(answers, output_path)
            elif choice == "4":
                output_path = metadata_output_path(input_path, "_chapters_removed")
                cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path), "-map", "0", "-map_chapters", "-1", "-c", "copy", str(output_path)]
                if confirm_and_run_ffmpeg(answers, cmd, "Chapter Removal", output_path):
                    metadata_set_current_input(answers, output_path)
            else:
                error("Enter a menu number from 1 to 4.")
        except Back:
            continue


def metadata_attached_picture_streams(probe: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        stream for stream in (probe.get("streams") or [])
        if metadata_stream_type(stream) == "video"
        and int((stream.get("disposition") or {}).get("attached_pic") or 0)
    ]


def run_cover_picture_editor(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Cover / Attached Picture Editor", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Add cover image", default=True))
        print(metadata_menu_item(2, "Replace existing cover image"))
        print(metadata_menu_item(3, "Remove attached pictures"))
        print(metadata_menu_item(4, "Show attached picture streams"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            return
        try:
            input_path = answers["metadata_input_path"]
            probe = metadata_refresh_probe(answers)
            attached = metadata_attached_picture_streams(probe)
            if choice == "4":
                if not attached:
                    note("No attached picture streams were found.")
                for stream in attached:
                    print("  " + metadata_stream_line(probe, stream))
                continue
            if choice in {"1", "2"}:
                cover = terminal_path(ask_required(metadata_prompt(answers, "Enter cover image path", "jpg, jpeg, png, or webp")))
                if not cover.exists() or not cover.is_file():
                    error("Cover image was not found.")
                    continue
                if cover.suffix.lower().lstrip(".") not in {"jpg", "jpeg", "png", "webp"}:
                    error("Unsupported cover image extension. Use jpg, jpeg, png, or webp.")
                    continue
                if input_path.suffix.lower() in {".mp4", ".m4a", ".m4v", ".mov"} and cover.suffix.lower() not in {".jpg", ".jpeg"}:
                    note("MP4-like containers usually expect JPEG cover art.")
                output_path = metadata_output_path(input_path, "_cover_replaced" if choice == "2" else "_cover_added")
                cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path), "-i", str(cover), "-map", "0"]
                if choice == "2":
                    for stream in attached:
                        cmd.extend(["-map", f"-0:v:{metadata_stream_relative_index(probe, stream)}"])
                video_count_after_removal = len([s for s in probe.get("streams") or [] if metadata_stream_type(s) == "video"]) - (len(attached) if choice == "2" else 0)
                attached_idx = max(0, video_count_after_removal)
                cmd.extend(["-map", "1", "-c", "copy", f"-c:v:{attached_idx}", "mjpeg", f"-disposition:v:{attached_idx}", "attached_pic", str(output_path)])
                note("Attached picture support varies by container. This operation uses stream copy and only converts the added image stream when FFmpeg requires MJPEG.")
                if confirm_and_run_ffmpeg(answers, cmd, "Cover / Attached Picture Editor", output_path):
                    metadata_set_current_input(answers, output_path)
            elif choice == "3":
                if not attached:
                    note("No attached picture streams were found.")
                    continue
                output_path = metadata_output_path(input_path, "_cover_removed")
                cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path), "-map", "0"]
                for stream in attached:
                    cmd.extend(["-map", f"-0:v:{metadata_stream_relative_index(probe, stream)}"])
                cmd.extend(["-c", "copy", str(output_path)])
                if confirm_and_run_ffmpeg(answers, cmd, "Attached Picture Removal", output_path):
                    metadata_set_current_input(answers, output_path)
            else:
                error("Enter a menu number from 1 to 4.")
        except Back:
            continue


def metadata_filter_path(path: Path) -> str:
    text = str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    return "'" + text + "'"


def build_signalstats_command(
    input_path: Path,
    video_stream_index: int,
    sampling_mode: str,
    ffmpeg: str,
    output_path: Path,
    use_cuda_decode: bool = False,
) -> list[str]:
    step = {"fast": 120, "balanced": 30, "detailed": 5}.get(sampling_mode, 30)
    vf = f"select='not(mod(n,{step}))',signalstats,metadata=mode=print:file={metadata_filter_path(output_path)}"
    args = [ffmpeg, "-hide_banner", "-nostats", "-v", "warning"]
    if use_cuda_decode:
        args.extend(["-hwaccel", "cuda", "-hwaccel_device", str(GPU_DEVICE_INDEX)])
    args.extend(["-i", str(input_path), "-map", f"0:{video_stream_index}", "-vf", vf, "-an", "-sn", "-f", "null", os.devnull])
    return args


def estimate_color_range(
    input_path: Path,
    video_stream_index: int,
    sampling_mode: str,
    ffmpeg: str,
    use_cuda_decode: bool = False,
) -> dict[str, Any]:
    output_path = metadata_report_output_path(input_path, f"_color_range_signalstats_stream{video_stream_index}", ".txt")
    args = build_signalstats_command(input_path, video_stream_index, sampling_mode, ffmpeg, output_path, use_cuda_decode)
    log_info("Metadata Editor signalstats command: " + command_to_powershell(args))
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0 and use_cuda_decode:
        log_error("Color range signalstats CUDA decode failed; retrying with CPU decode:\n" + (result.stderr or result.stdout or ""))
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        args = build_signalstats_command(input_path, video_stream_index, sampling_mode, ffmpeg, output_path, False)
        log_info("Metadata Editor signalstats CPU fallback command: " + command_to_powershell(args))
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        log_error("Color range signalstats failed:\n" + (result.stderr or result.stdout or ""))
        raise RuntimeError("Color range estimation failed. See log file.")
    values: dict[str, list[float]] = {"YMIN": [], "YLOW": [], "YAVG": [], "YHIGH": [], "YMAX": []}
    if output_path.exists():
        for line in output_path.read_text(encoding="utf-8", errors="replace").splitlines():
            for key in values:
                needle = f"lavfi.signalstats.{key}="
                if needle in line:
                    try:
                        values[key].append(float(line.split("=", 1)[1].strip()))
                    except ValueError:
                        pass
    frames = max((len(item) for item in values.values()), default=0)
    ymin = min(values["YMIN"]) if values["YMIN"] else None
    ymax = max(values["YMAX"]) if values["YMAX"] else None
    ylow = sum(values["YLOW"]) / len(values["YLOW"]) if values["YLOW"] else None
    yhigh = sum(values["YHIGH"]) / len(values["YHIGH"]) if values["YHIGH"] else None
    conclusion = "Estimated range: uncertain"
    if ymin is not None and ymax is not None:
        if ymin >= 8 and ymax <= 247:
            conclusion = "Estimated range: probably limited/TV range"
        elif ymin <= 5 or ymax >= 250:
            conclusion = "Estimated range: possibly full/PC range"
    return {"frames": frames, "ymin": ymin, "ymax": ymax, "ylow": ylow, "yhigh": yhigh, "report_path": str(output_path), "conclusion": conclusion}


def metadata_bsf_name(codec_name: str) -> str | None:
    codec = str(codec_name or "").lower()
    if codec in {"h264", "avc1"}:
        return "h264_metadata"
    if codec in {"hevc", "h265"}:
        return "hevc_metadata"
    return None


def run_video_bitstream_metadata_tools(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Video Bitstream Metadata Tools", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Inspect declared video color metadata", default=True))
        print(metadata_menu_item(2, "Estimate actual color range from pixel values"))
        print(metadata_menu_item(3, "Set H.264 video_full_range_flag"))
        print(metadata_menu_item(4, "Set HEVC video_full_range_flag"))
        print(metadata_menu_item(5, "Set H.264 color primaries / transfer / matrix"))
        print(metadata_menu_item(6, "Set HEVC color primaries / transfer / matrix"))
        print(metadata_menu_item(7, "Set H.264 sample aspect ratio"))
        print(metadata_menu_item(8, "Set HEVC sample aspect ratio"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            return
        if choice not in {str(i) for i in range(1, 9)}:
            error("Enter a menu number from 1 to 8.")
            continue
        try:
            input_path = answers["metadata_input_path"]
            probe = metadata_refresh_probe(answers)
            video_streams = [s for s in probe.get("streams") or [] if metadata_stream_type(s) == "video"]
            if choice == "1":
                for stream in video_streams:
                    print("  " + metadata_stream_line(probe, stream))
                continue
            if len(video_streams) == 1:
                stream = video_streams[0]
                list_streams_for_selection(probe, video_streams)
                stream_index = metadata_stream_index(stream)
                note(f"Using the only video stream: {stream_index}.")
                log_info(
                    f"Metadata Editor auto-selected only video stream: index={stream_index}; "
                    f"spec={metadata_stream_spec(probe, stream)}"
                )
            else:
                stream = select_stream(probe, answers, {"video"})
            if choice == "2":
                mode = ask_raw(metadata_prompt(answers, "Choose sampling density", "1=fast every 120th frame; 2=balanced every 30th frame; 3=detailed every 5th frame", "2"))
                if is_back_value(mode):
                    raise Back()
                sampling = {"1": "fast", "2": "balanced", "3": "detailed", "": "balanced"}.get(mode, "balanced")
                if gpu_available_for_answers(answers):
                    use_cuda_decode = ask_yes_no(
                        metadata_prompt(
                            answers,
                            "Use CUDA/NVDEC GPU decode for this analysis?",
                            "signalstats analysis still runs on CPU; GPU decode only reduces decode load when supported",
                            "y",
                        ),
                        True,
                    )
                else:
                    use_cuda_decode = False
                    note("No usable NVIDIA/NVENC GPU was detected. CUDA decode question skipped.")
                result = estimate_color_range(
                    input_path,
                    int(metadata_stream_index(stream) or 0),
                    sampling,
                    answers["ffmpeg"],
                    use_cuda_decode=use_cuda_decode,
                )
                print()
                print(paint("Color range estimate", Color.BOLD + Color.LIGHT_BLUE))
                print("  " + field_text("Declared color_range", stream.get("color_range") or "unknown", Color.COLOR_RANGE_VALUE))
                print("  " + field_text("Observed YMIN", result["ymin"], Color.YELLOW))
                print("  " + field_text("Observed YMAX", result["ymax"], Color.YELLOW))
                print("  " + field_text("Observed YLOW average", result["ylow"], Color.CYAN))
                print("  " + field_text("Observed YHIGH average", result["yhigh"], Color.CYAN))
                print("  " + field_text("Sampled frames", result["frames"], Color.GREEN))
                print("  " + field_text("Conclusion", result["conclusion"], Color.MAGENTA))
                note("This is an approximation based on decoded pixel statistics. It is not a 100% reliable proof of the original intended color range.")
                continue
            required_codec = {"3": {"h264", "avc1"}, "5": {"h264", "avc1"}, "7": {"h264", "avc1"}, "4": {"hevc", "h265"}, "6": {"hevc", "h265"}, "8": {"hevc", "h265"}}[choice]
            codec = str(stream.get("codec_name") or "").lower()
            if codec not in required_codec:
                error("This operation is only available for the matching H.264 or HEVC codec.")
                continue
            bsf = metadata_bsf_name(codec)
            if not bsf:
                error("This video codec is not supported by this bitstream metadata tool.")
                continue
            note("This changes metadata/signaling only. It does not truly convert the video pixels. Wrong values can cause washed-out image or crushed blacks.")
            if not ask_yes_no(metadata_prompt(answers, "Continue with this advanced bitstream metadata change?", "y/n", "n"), False):
                continue
            if choice in {"3", "4"}:
                value = ask_raw(metadata_prompt(
                    answers,
                    "Choose video_full_range_flag",
                    "0=limited/TV; 1=full/PC; use b to go back",
                    back="back=b, quit=exit",
                ))
                if value.lower().strip() in {"b", "back"}:
                    raise Back()
                if value not in {"0", "1"}:
                    error("Enter 0 or 1.")
                    continue
                suffix = "_colorflag_full" if value == "1" else "_colorflag_limited"
                filter_arg = f"{bsf}=video_full_range_flag={value}"
            elif choice in {"5", "6"}:
                note("Common values: BT.709 = 1/1/1; BT.2020 SDR/PQ commonly uses primaries=9, transfer=14 or 16, matrix=9; SMPTE 170M/SD = 6/6/6.")
                primaries = metadata_value_prompt(answers, "Enter colour_primaries numeric value")
                transfer = metadata_value_prompt(answers, "Enter transfer_characteristics numeric value")
                matrix = metadata_value_prompt(answers, "Enter matrix_coefficients numeric value")
                suffix = "_color_metadata"
                filter_arg = f"{bsf}=colour_primaries={primaries}:transfer_characteristics={transfer}:matrix_coefficients={matrix}"
            else:
                sar = metadata_value_prompt(answers, "Enter sample aspect ratio")
                suffix = "_sample_aspect_ratio"
                filter_arg = f"{bsf}=sample_aspect_ratio={sar}"
            output_path = metadata_output_path(input_path, suffix)
            spec = metadata_stream_spec(probe, stream)
            cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path), "-map", "0", "-c", "copy", f"-bsf:{spec}", filter_arg, str(output_path)]
            if confirm_and_run_ffmpeg(answers, cmd, "Video Bitstream Metadata Tool", output_path):
                metadata_set_current_input(answers, output_path)
        except Back:
            continue


def metadata_json_report_command(ffprobe: str, input_path: Path, report_type: str) -> list[str]:
    if report_type == "tags":
        return [ffprobe, "-hide_banner", "-v", "error", "-show_entries", "format_tags:stream_tags:chapters", "-of", "json", str(input_path)]
    if report_type == "color":
        return [ffprobe, "-hide_banner", "-v", "error", "-select_streams", "v", "-show_entries", "stream=index,codec_name,pix_fmt,bits_per_raw_sample,color_range,color_space,color_transfer,color_primaries,width,height", "-of", "json", str(input_path)]
    if report_type == "disposition":
        return [ffprobe, "-hide_banner", "-v", "error", "-show_entries", "stream=index,codec_type,codec_name:stream_disposition:stream_tags", "-of", "json", str(input_path)]
    if report_type == "chapters":
        return [ffprobe, "-hide_banner", "-v", "error", "-show_chapters", "-of", "json", str(input_path)]
    return [ffprobe, "-hide_banner", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json", str(input_path)]


def write_metadata_report(input_path: Path, report_type: str, answers: dict[str, Any]) -> Path:
    if report_type == "human":
        probe = probe_media_json(input_path, answers["ffprobe"])
        lines = [f"Metadata report for: {input_path}", "", "Streams"]
        lines.extend("  " + _strip_ansi(metadata_stream_line(probe, stream)) for stream in probe.get("streams") or [])
        lines.extend(["", "Chapters"])
        lines.extend("  " + line for line in metadata_chapter_lines(probe))
        output_path = metadata_report_output_path(input_path, "_metadata_report", ".txt")
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output_path
    args = metadata_json_report_command(answers["ffprobe"], input_path, report_type)
    log_info("Metadata report ffprobe command: " + command_to_powershell(args))
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        log_error("Metadata report ffprobe failed:\n" + (result.stderr or result.stdout or ""))
        raise RuntimeError("Metadata report failed. See log file.")
    suffix = {"full": "_metadata_report", "tags": "_metadata_tags", "color": "_metadata_color", "disposition": "_metadata_disposition", "chapters": "_metadata_chapters"}.get(report_type, "_metadata_report")
    output_path = metadata_report_output_path(input_path, suffix, ".json")
    output_path.write_text(result.stdout, encoding="utf-8")
    return output_path


def run_metadata_report_inspect(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Metadata Report / Inspect", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Full ffprobe JSON report", default=True))
        print(metadata_menu_item(2, "Human-readable stream report"))
        print(metadata_menu_item(3, "Tags-only report"))
        print(metadata_menu_item(4, "Color metadata report"))
        print(metadata_menu_item(5, "Disposition report"))
        print(metadata_menu_item(6, "Chapter report"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            return
        report_type = {"1": "full", "2": "human", "3": "tags", "4": "color", "5": "disposition", "6": "chapters"}.get(choice)
        if not report_type:
            error("Enter a menu number from 1 to 6.")
            continue
        try:
            output_path = write_metadata_report(answers["metadata_input_path"], report_type, answers)
            note(f"Metadata report written: {output_path}")
            log_info(f"Metadata report written: type={report_type}; path={output_path}")
        except Exception as exc:
            log_exception("Metadata report failed")
            error(str(exc))


def run_metadata_editor_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        answers = metadata_prompt_input(base_answers)
    except Back:
        note("Returning to main menu.")
        return None
    while True:
        print()
        print(paint("Metadata Editor", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Stream Metadata Editor", default=True))
        print(metadata_menu_item(2, "Stream Disposition Editor"))
        print(metadata_menu_item(3, "Chapter Metadata Editor"))
        print(metadata_menu_item(4, "Cover / Attached Picture Editor"))
        print(metadata_menu_item(5, "Video Bitstream Metadata Tools"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            note("Returning to main menu.")
            return None
        try:
            if choice == "1":
                run_stream_metadata_editor(answers)
            elif choice == "2":
                run_stream_disposition_editor(answers)
            elif choice == "3":
                run_chapter_metadata_editor(answers)
            elif choice == "4":
                run_cover_picture_editor(answers)
            elif choice == "5":
                run_video_bitstream_metadata_tools(answers)
            else:
                error("Enter a menu number from 1 to 5.")
        except ExitWizard:
            raise
        except Exception as exc:
            log_exception("Metadata Editor operation failed")
            error(str(exc))


def ask_main_menu(answers: dict[str, Any], config_path: Path) -> int:
    print()
    print(paint("FFmWiz Main menu:", Color.BOLD + Color.LIGHT_BLUE))
    print(f"  {paint('1.', Color.LIGHT_BLUE)} Interactive wizard {paint('[1]', Color.GREEN)}")
    print(f"  {paint('2.', Color.LIGHT_BLUE)} Load config and ask crop only")
    print(f"  {paint('3.', Color.LIGHT_BLUE)} Cut video only with copy")
    print(f"  {paint('4.', Color.LIGHT_BLUE)} Folder Encode")
    print(f"  {paint('5.', Color.LIGHT_BLUE)} Add files to video")
    print(f"  {paint('6.', Color.LIGHT_BLUE)} Extract Stream")
    print(f"  {paint('7.', Color.LIGHT_BLUE)} Media info report")
    print(f"  {paint('8.', Color.LIGHT_BLUE)} Stream Cleanup Remux")
    print(f"  {paint('9.', Color.LIGHT_BLUE)} Hard Sub Encode")
    print(f"  {paint('10.', Color.LIGHT_BLUE)} Video Speed / Reverse")
    print(f"  {paint('11.', Color.LIGHT_BLUE)} Audio Cut / Speed / Reverse")
    print(f"  {paint('12.', Color.LIGHT_BLUE)} Join Videos")
    print(f"  {paint('13.', Color.LIGHT_BLUE)} Metadata Editor")
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
        if value in {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13"}:
            return int(value)
        error("Enter a menu number from 1 to 13.")


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
        while idx > 0 and (not steps[idx].applicable(answers) or step_is_auto_back_skip(steps[idx], answers)):
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
        if is_back_value(value):
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
        1 -> enter cut times manually with h:m:s:frame
    """
    print()
    print(paint("Cut video only with copy:", Color.BOLD + Color.LIGHT_BLUE))
    print(f"  {paint('1.', Color.LIGHT_BLUE)} Manual cut using h:m:s:frame {paint('[default]', Color.GREEN)}")
    print()
    while True:
        value = ask_raw(
            f"{paint('Selection', Color.BOLD)} {paint('[1]', Color.GREEN)} "
            f"{back_text('0=back, quit=exit')}: "
        )
        if not value:
            return 1
        if is_back_value(value):
            raise Back()
        if value == "1":
            return int(value)
        if value.lower() in {"g", "gui", "graphical"}:
            error("The standalone Cut GUI is archived. Use manual cut in this mode.")
            continue
        error("Enter 1.")


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
        if is_back_value(value):
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


def format_split_points_for_summary(points: list[float], fps: float, label: str = "Split points") -> str:
    pts = sorted(float(p) for p in (points or []))
    if not pts:
        return f"{label}: (none)"
    lines = [f"{label}: {len(pts)} (output split into {len(pts) + 1} parts):"]
    for idx, t in enumerate(pts, start=1):
        lines.append(f"  {idx}. {seconds_to_hmsf(t, fps)}  ({seconds_to_ffmpeg_time(t)})")
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
        if is_back_value(value):
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
                try:
                    keep_ranges = collect_cut_ranges_terminal(answers, fps, duration)
                except Back:
                    note("Returning to the cut-method menu.")
                    continue
            else:
                error("The standalone Cut GUI is archived. Use manual cut in this mode.")
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
                format_cut_ranges_for_summary(keep_ranges, fps, "Cuts (keep ranges)"),
                Color.LIME,
            ))
        return
    while True:
        hint_text = "y/n; cuts are applied frame-accurate via filter_complex"
        value = ask_raw(
            question_prompt(
                answers,
                "Apply cuts before encoding?",
                hint_text,
                "n",
            )
        )
        if is_back_value(value):
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
            error("The standalone Cut GUI is archived. Use the Unified Video Editor or enter cuts manually.")
            continue
        else:
            error("Enter y or n.")
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
        if is_back_value(value):
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
        Step("nvenc_multipass", nvenc_multipass_prompt_applicable, step_nvenc_multipass),
        Step("resolution", video_reencode_options_applicable, step_resolution),
        Step("fps", video_reencode_options_applicable, step_fps),
        Step("video_speed_reverse", output_has_video, step_video_speed_reverse_for_encode),
        Step("audio_tracks", lambda a: bool(a.get("audio_streams")), step_audio_tracks),
        Step("loudnorm", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_loudnorm),
        Step("audio_cut", audio_only_transform_prompt_applicable, step_audio_cut_for_encode),
        Step("audio_speed_reverse", audio_only_transform_prompt_applicable, step_audio_speed_reverse_for_encode),
        Step("audio_codec", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_audio_codec),
        Step("audio_bitrate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy" and audio_codec_uses_bitrate(str(a.get("audio_codec") or default_audio_codec_for_ext(a.get("output_ext", "")))), step_audio_bitrate),
        Step("source_extras", source_extra_policy_applicable, step_source_extra_policy),
        Step("subtitle_tracks", lambda a: output_has_video(a) and source_subtitles_keep_enabled(a) and bool(a.get("subtitle_streams")), step_subtitle_tracks),
        Step("start_now", lambda a: True, step_start_folder_now),
    ]

    def next_index(start: int) -> int:
        idx = start
        while idx < len(steps) and not steps[idx].applicable(answers):
            idx += 1
        return idx

    def prev_index(start: int) -> int:
        idx = start
        while idx > 0 and (
            not steps[idx].applicable(answers)
            or is_auto_unified_crop_step(idx)
            or step_is_auto_back_skip(steps[idx], answers)
        ):
            idx -= 1
        return max(0, idx)

    def is_auto_unified_crop_step(pos: int) -> bool:
        return bool(
            answers.get("_unified_video_editor_used")
            and steps[pos].name in {"crop_enabled", "crop_top", "crop_left", "crop_right", "crop_bottom"}
        )

    def question_number(current: int) -> int:
        count = 0
        for pos in range(current + 1):
            if steps[pos].applicable(answers) and not is_auto_unified_crop_step(pos):
                count += 1
        return answers.get("_question_offset", 0) + count

    idx = next_index(0)
    while idx < len(steps):
        try:
            answers["_question_number"] = question_number(idx)
            steps[idx].run(answers)
            apply_unified_video_editor_answers(answers)
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


def probe_additional_track_file(ffprobe: str, path: Path, ffmpeg: str | None = None) -> dict[str, Any]:
    probe = ffprobe_json(ffprobe, path)
    streams = probe.get("streams") or []
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    if not audio_streams and not subtitle_streams:
        raise ValueError("Additional files must contain at least one audio or subtitle stream.")
    audio_volume_stats: dict[int, dict[str, str]] = {}
    if audio_streams and ffmpeg:
        audio_volume_stats = get_audio_volume_stats({
            "ffmpeg": ffmpeg,
            "input_path": path,
            "audio_streams": audio_streams,
        })
    return {
        "path": path,
        "format": probe.get("format", {}),
        "audio_streams": audio_streams,
        "audio_volume_stats": audio_volume_stats,
        "subtitle_streams": subtitle_streams,
        "video_streams": video_streams,
        "chapters": probe.get("chapters") or [],
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
        volume_stats = item.get("audio_volume_stats") or {}
        for idx, stream in enumerate(audio_streams):
            rate = stream_bitrate_kbps(stream, fmt)
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
                f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, idx), Color.MEAN_VOLUME)} | "
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
            chapters_value, chapters_color = chapter_presence(item)
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('type', stream_type, Color.ORANGE)} | "
                f"{field_text('size', str(width) + 'x' + str(height), Color.LIME)} | "
                f"{field_text('bit depth', describe_video_bit_depth(stream), Color.PINK)} | "
                f"{field_text('Color range', display_color_range(stream.get('color_range')), Color.COLOR_RANGE_VALUE)} | "
                f"{field_text('duration', duration, Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('total bitrate', describe_total_bitrate(fmt), Color.AQUA)} | "
                f"{field_text('chapters', chapters_value, chapters_color)}"
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
        if is_back_value(value):
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
        if is_back_value(value):
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
            item = probe_additional_track_file(answers["ffprobe"], path, answers.get("ffmpeg"))
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


EXTRACT_AUDIO_EXTENSIONS = {
    "aac": ".aac",
    "ac3": ".ac3",
    "eac3": ".eac3",
    "mp3": ".mp3",
    "mp2": ".mp2",
    "opus": ".opus",
    "vorbis": ".ogg",
    "flac": ".flac",
    "alac": ".m4a",
    "pcm_s16le": ".wav",
    "pcm_s24le": ".wav",
    "pcm_s32le": ".wav",
    "pcm_f32le": ".wav",
    "dts": ".dts",
    "truehd": ".thd",
}
EXTRACT_SUBTITLE_EXTENSIONS = {
    "ass": ".ass",
    "ssa": ".ssa",
    "subrip": ".srt",
    "srt": ".srt",
    "text": ".srt",
    "mov_text": ".srt",
    "webvtt": ".vtt",
    "hdmv_pgs_subtitle": ".sup",
    "pgs": ".sup",
    "dvd_subtitle": ".sub",
    "dvdsub": ".sub",
    "vobsub": ".sub",
    "dvb_subtitle": ".sub",
    "dvbsub": ".sub",
    "xsub": ".avi",
}


def stream_global_index(stream: dict[str, Any]) -> int | None:
    try:
        return int(stream.get("index"))
    except (TypeError, ValueError):
        return None


def extract_stream_candidates(answers: dict[str, Any]) -> list[dict[str, Any]]:
    streams = answers.get("probe", {}).get("streams") or []
    candidates = [
        stream for stream in streams
        if stream.get("codec_type") in {"video", "audio", "subtitle"} and stream_global_index(stream) is not None
    ]
    return sorted(candidates, key=lambda stream: int(stream.get("index", 0)))


def extract_stream_default_extension(stream: dict[str, Any]) -> str:
    codec_type = str(stream.get("codec_type") or "").lower()
    codec = str(stream.get("codec_name") or "").lower()
    if codec_type == "audio":
        return EXTRACT_AUDIO_EXTENSIONS.get(codec, ".mka")
    if codec_type == "subtitle":
        return EXTRACT_SUBTITLE_EXTENSIONS.get(codec, ".srt")
    if codec_type == "video":
        return ".mkv"
    return ".bin"


def extract_stream_codec_args(stream: dict[str, Any]) -> tuple[list[str], str]:
    codec_type = str(stream.get("codec_type") or "").lower()
    codec = str(stream.get("codec_name") or "").lower()
    if codec_type == "subtitle" and codec in {"mov_text", "text"}:
        return ["-c:s", "srt"], "text subtitle converted to SRT for extraction"
    return ["-c", "copy"], "stream copy"


def default_extract_stream_output_path(input_path: Path, stream: dict[str, Any]) -> Path:
    stream_index = stream_global_index(stream)
    codec_type = str(stream.get("codec_type") or "stream").lower()
    suffix = extract_stream_default_extension(stream)
    stem = f"{sanitize_output_stem(input_path.stem)}{EXTRACT_STREAM_OUTPUT_SUFFIX}{stream_index}_{codec_type}"
    return input_path.parent / f"{stem}{suffix}"


def choose_extract_stream_output_path(input_path: Path, stream: dict[str, Any], value: str) -> Path:
    default_path = default_extract_stream_output_path(input_path, stream)
    default_suffix = default_path.suffix
    if not value:
        candidate = default_path
    else:
        output_value = terminal_path(value)
        if not output_value.drive and not output_value.root and output_value.parent == Path("."):
            if output_value.suffix:
                candidate = input_path.parent / sanitize_output_stem(output_value.stem)
                candidate = candidate.with_suffix(output_value.suffix)
            else:
                candidate = input_path.parent / f"{sanitize_output_stem(output_value.name)}{default_suffix}"
        elif output_value.suffix:
            candidate = output_value.with_name(f"{sanitize_output_stem(output_value.stem)}{output_value.suffix}")
        else:
            candidate = output_value / default_path.name
    candidate = resolve_output_collision_against_inputs(candidate, [input_path], "_Extract")
    return unique_numbered_path(candidate)


def extract_stream_description(stream: dict[str, Any], answers: dict[str, Any]) -> str:
    codec_type = str(stream.get("codec_type") or "unknown")
    codec = str(stream.get("codec_name") or "unknown")
    stream_index = stream_global_index(stream)
    duration = format_duration(stream_duration_seconds(stream, answers.get("format")))
    packet_sizes = get_packet_sizes(answers)
    color = {"video": Color.MAGENTA, "audio": Color.BLUE, "subtitle": Color.LIGHT_YELLOW}.get(codec_type, Color.WHITE)
    parts = [
        field_text("stream index", stream_index if stream_index is not None else "unknown", Color.LIGHT_BLUE),
        field_text("type", codec_type, color),
        field_text("codec", codec, Color.CYAN),
        field_text("duration", duration, Color.MAGENTA),
    ]
    if codec_type == "video":
        parts.append(field_text("size", f"{stream.get('width', '?')}x{stream.get('height', '?')}", Color.LIME))
        fps = rational_to_float(stream.get("avg_frame_rate"))
        parts.append(field_text("fps", format(fps, ".3g") if fps else "unknown", Color.MAGENTA))
    elif codec_type == "audio":
        parts.append(field_text("channels", stream.get("channels", "unknown"), Color.GREEN))
        parts.append(field_text("sample_rate", stream.get("sample_rate", "unknown"), Color.MAGENTA))
        parts.append(field_text("bitrate", describe_bitrate(stream_bitrate_kbps(stream, answers.get("format"), packet_sizes)), Color.YELLOW))
    elif codec_type == "subtitle":
        lang = display_language(stream.get("tags", {}).get("language"))
        title = stream.get("tags", {}).get("title") or "unknown"
        parts.append(field_text("language", lang or "unknown", Color.AQUA))
        parts.append(field_text("title", title, Color.WHITE))
    return " | ".join(parts)


def print_extract_stream_candidates(answers: dict[str, Any]) -> None:
    print()
    print(paint("Extractable streams", Color.BOLD + Color.LIGHT_BLUE))
    for stream in extract_stream_candidates(answers):
        print("  " + extract_stream_description(stream, answers))


def step_extract_stream_input(answers: dict[str, Any]) -> None:
    step_input_path(answers)
    print_source_info(answers)


def step_extract_stream_index(answers: dict[str, Any]) -> None:
    candidates = extract_stream_candidates(answers)
    if not candidates:
        raise ValueError("No extractable video, audio, or subtitle streams were found.")
    print_extract_stream_candidates(answers)
    by_index = {stream_global_index(stream): stream for stream in candidates}
    while True:
        value = ask_raw(
            question_prompt(
                answers,
                "Enter stream index to extract",
                "use the ffprobe stream index shown above; 0 is stream index 0 here",
                back="back=b, quit=exit",
            )
        )
        lowered = value.lower().strip()
        if lowered in {"b", "back"}:
            raise Back()
        if not value:
            error("Enter a stream index.")
            continue
        if not re.fullmatch(r"\d+", value):
            error("Enter a numeric stream index from the list above.")
            continue
        stream_index = int(value)
        stream = by_index.get(stream_index)
        if stream is None:
            allowed = ", ".join(str(idx) for idx in sorted(index for index in by_index if index is not None))
            error(f"Stream index {stream_index} was not found. Available indexes: {allowed}")
            continue
        answers["extract_stream"] = stream
        answers["extract_stream_index"] = stream_index
        log_info(
            "User choice: extract_stream_index="
            f"{stream_index}; type={stream.get('codec_type')}; codec={stream.get('codec_name')}"
        )
        return


def step_extract_stream_output_path(answers: dict[str, Any]) -> None:
    stream = answers["extract_stream"]
    default_path = default_extract_stream_output_path(answers["input_path"], stream)
    folder_example = example_text(r"E:\output")
    name_example = example_text('"Extracted track"')
    value = ask_raw(
        question_prompt(
            answers,
            "Enter extracted stream output path, output folder, or bare output name",
            f"Enter={default_path.name}; examples: {folder_example} or {name_example}",
        )
    )
    if is_back_value(value):
        raise Back()
    answers["extract_output_path"] = choose_extract_stream_output_path(answers["input_path"], stream, value)
    log_info(f"Resolved extracted stream output path: {answers['extract_output_path']}")


def build_extract_stream_command(ffmpeg: str, input_path: Path, stream: dict[str, Any], output_path: Path) -> list[str]:
    stream_index = stream_global_index(stream)
    if stream_index is None:
        raise ValueError("Selected stream has no ffprobe stream index.")
    codec_type = str(stream.get("codec_type") or "").lower()
    codec_args, _mode = extract_stream_codec_args(stream)
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(input_path), "-map", f"0:{stream_index}"]
    if codec_type == "video":
        cmd.extend(["-an", "-sn", "-dn"])
    elif codec_type == "audio":
        cmd.extend(["-vn", "-sn", "-dn"])
    elif codec_type == "subtitle":
        cmd.extend(["-vn", "-an", "-dn"])
    cmd.extend(codec_args)
    cmd.append(str(output_path))
    return cmd


def print_extract_stream_summary(answers: dict[str, Any], cmd: list[str]) -> None:
    stream = answers["extract_stream"]
    output_path = answers["extract_output_path"]
    _codec_args, mode = extract_stream_codec_args(stream)
    print()
    print(paint("Extract Stream summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("Input", answers["input_path"], Color.WHITE))
    print("  " + field_text("Selected stream", f"#{answers['extract_stream_index']}", Color.LIGHT_BLUE))
    print("  " + field_text("Type", stream.get("codec_type", "unknown"), Color.MAGENTA))
    print("  " + field_text("Codec", stream.get("codec_name", "unknown"), Color.CYAN))
    print("  " + field_text("Extraction mode", mode, Color.YELLOW))
    print("  " + field_text("Output", output_path, Color.LIME))
    print()
    print(paint("Final PowerShell command:", Color.FINAL_COMMAND_LABEL))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    log_info("Extract Stream summary: " + json.dumps({
        "input": str(answers["input_path"]),
        "stream_index": answers["extract_stream_index"],
        "codec_type": stream.get("codec_type"),
        "codec": stream.get("codec_name"),
        "mode": mode,
        "output": str(output_path),
    }, ensure_ascii=False))
    log_info("Final PowerShell command: " + command_to_powershell(cmd))


def step_extract_stream_start_now(answers: dict[str, Any]) -> None:
    cmd = build_extract_stream_command(
        answers["ffmpeg"],
        answers["input_path"],
        answers["extract_stream"],
        answers["extract_output_path"],
    )
    answers["cmd"] = cmd
    print_extract_stream_summary(answers, cmd)
    answers["start_now"] = ask_yes_no(
        question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def run_extract_stream_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    steps = [
        Step("input_path", lambda a: True, step_extract_stream_input),
        Step("extract_stream_index", lambda a: True, step_extract_stream_index),
        Step("extract_output_path", lambda a: True, step_extract_stream_output_path),
        Step("start_now", lambda a: True, step_extract_stream_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except Back:
        note("Returning to main menu.")
        return None
    if not answers.get("start_now", True):
        note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    output_path = Path(answers["extract_output_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = stream_duration_seconds(answers.get("extract_stream", {}), answers.get("format"))
    log_info(
        f"Extract Stream starting: input={answers['input_path']}; "
        f"stream_index={answers.get('extract_stream_index')}; output={output_path}"
    )
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(duration if duration and duration > 0 else None),
        label="Extract Stream",
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
        filters.append(
            "format=" + (cuda_pixel_format_for_output(answers) if video_encoder.endswith("_nvenc") else cpu_pixel_format_for_output(answers))
        )
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
    video_encoder, tag, _profile = enforce_bit_depth_compatible_video_encoder(answers, video_encoder, tag, _profile)
    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n", "-i", str(input_path)]

    cmd.extend(["-map", "0:v:0"])
    audio_mode = answers.get("hardsub_audio_mode", "copy-all")
    audio_policy = answers.get("hardsub_audio_container_policy")
    mapped_audio_output_count = 0
    if audio_mode != "none" and input_ext != output_ext and not audio_policy:
        raise ValueError("HardSub audio container policy is required when output container differs from the source container.")
    if audio_policy == "match-source-container":
        audio_policy = "copy-anyway"
    if input_ext == output_ext and not audio_policy:
        audio_policy = "copy-anyway"
    if audio_mode == "none" or audio_policy == "none":
        cmd.append("-an")
    elif audio_mode == "selected":
        selected_hardsub_audio = list(answers.get("hardsub_audio_tracks", []))
        for index in selected_hardsub_audio:
            cmd.extend(["-map", f"0:a:{index}"])
        mapped_audio_output_count = len(selected_hardsub_audio)
        if audio_policy == "aac":
            bitrate = int(answers.get("hardsub_audio_bitrate_kbps") or DEFAULT_AUDIO_BITRATE_KBPS)
            cmd.extend(["-c:a", "aac", "-b:a", f"{bitrate}k", "-ac", "2"])
        else:
            cmd.extend(["-c:a", "copy"])
    else:
        cmd.extend(["-map", "0:a?"])
        mapped_audio_output_count = len(answers.get("audio_streams") or [])
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
    append_nvenc_multipass_args(cmd, answers, video_encoder)
    append_hardsub_color_args(cmd, answers)
    if video_encoder == "hevc_nvenc":
        cmd.extend(["-profile:v", hevc_profile_for_output(answers, "main")])
    elif video_encoder == "libx265":
        cmd.extend(["-profile:v", hevc_profile_for_output(answers, "main")])
    append_clear_reencoded_stream_stat_metadata(
        cmd,
        answers,
        video_output_count=1,
        audio_output_count=mapped_audio_output_count,
        subtitle_output_count=0,
    )
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
    if is_back_value(value):
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
        if is_back_value(value):
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
            if is_back_value(value):
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
            if is_back_value(value):
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
        if is_back_value(value):
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
        if is_back_value(value):
            raise Back()
        if not value or value.lower() == "n":
            value = default_codec
        if value.lower() == "copy":
            error("Hard subtitles require video re-encoding; copy is not valid here.")
            continue
        answers["video_codec"] = value
        return


def step_hardsub_use_gpu(answers: dict[str, Any]) -> None:
    if not gpu_available_for_answers(answers):
        answers["use_gpu"] = False
        note("No usable NVIDIA/NVENC GPU was detected. GPU question skipped; CPU mode selected.")
        log_info("User choice: use_gpu=False; reason=GPU unavailable")
        return
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
        if is_back_value(value):
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
                if is_back_value(custom):
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
        if is_back_value(value):
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
        if is_back_value(value):
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
        if is_back_value(value):
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
    if "_nvenc" in command_to_text(cmd):
        print("  " + field_text("NVENC multipass", normalize_nvenc_multipass_mode(answers.get("nvenc_multipass")), Color.YELLOW))
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
        Step(
            "nvenc_multipass",
            lambda a: nvenc_multipass_prompt_applicable(a),
            lambda a: ask_nvenc_multipass_if_applicable(a, workflow_name="HardSub", quality_oriented=True),
        ),
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
            while idx > 0 and step_is_auto_back_skip(steps[idx], answers):
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
    video_sources: list[str]
    audio_sources: list[str] = []
    if len(keep_ranges) > 1:
        video_sources = [f"vsrc{idx}" for idx in range(len(keep_ranges))]
        fc_parts.append(f"[0:v:0]split={len(keep_ranges)}{''.join(f'[{label}]' for label in video_sources)}")
        log_info(f"Inserted split={len(keep_ranges)} for multi-range video trim from [0:v:0].")
        if audio_for_cut is not None:
            audio_sources = [f"asrc{idx}" for idx in range(len(keep_ranges))]
            fc_parts.append(
                f"[0:a:{audio_for_cut}]asplit={len(keep_ranges)}"
                f"{''.join(f'[{label}]' for label in audio_sources)}"
            )
            log_info(f"Inserted asplit={len(keep_ranges)} for multi-range audio trim from [0:a:{audio_for_cut}].")
    else:
        video_sources = ["0:v:0"]
        if audio_for_cut is not None:
            audio_sources = [f"0:a:{audio_for_cut}"]
    for idx, (start, end) in enumerate(keep_ranges):
        fc_parts.append(
            f"[{video_sources[idx]}]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[v{idx}]"
        )
        if audio_for_cut is not None:
            fc_parts.append(
                f"[{audio_sources[idx]}]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS[a{idx}]"
            )

    if len(keep_ranges) > 1:
        concat_inputs = ""
        for idx in range(len(keep_ranges)):
            concat_inputs += f"[v{idx}]"
            if audio_for_cut is not None:
                concat_inputs += f"[a{idx}]"
        if audio_for_cut is not None:
            if audio_speed_transform_enabled(answers) or loudnorm_transform_enabled(answers):
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
            if audio_speed_transform_enabled(answers) or loudnorm_transform_enabled(answers):
                fc_parts.append(f"[a0]{build_encode_audio_speed_filter(answers)}[a]")
            else:
                fc_parts.append("[a0]asetpts=PTS-STARTPTS[a]")

    # Apply the user's video filters (crop/fps/scale/setsar/setparams) after concat.
    user_video_filter = build_cpu_video_filter(answers)
    if user_video_filter:
        fc_parts.append(f"[{video_label}]{user_video_filter}[v]")
    else:
        fc_parts.append(f"[{video_label}]null[v]")
    return ";".join(fc_parts)


def print_startup_banner(config_path: Path, launcher_path: Path, answers: dict[str, Any] | None = None) -> None:
    _ = (config_path, launcher_path)
    startup_line("FFmpeg", "found.", Color.LIME)
    answers = answers or {}
    if answers.get("gpu_available"):
        model = answers.get("gpu_model") or "NVIDIA NVENC GPU"
        startup_line("GPU", f"detected - {model} (NVENC hardware encoding).", Color.GREEN, Color.GREEN)
    else:
        startup_line("GPU", "not detected - video will be encoded on the CPU.", Color.RED, Color.RED)


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


def ffconcat_quote_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", r"'\''")


def join_load_media_item(answers: dict[str, Any], path: Path) -> dict[str, Any]:
    probe = ffprobe_json(answers["ffprobe"], path)
    streams = probe.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    if not video_streams:
        raise ValueError("Join Videos requires video inputs.")
    return {
        "path": path,
        "probe": probe,
        "format": probe.get("format", {}),
        "streams": streams,
        "video_streams": video_streams,
        "audio_streams": audio_streams,
        "subtitle_streams": subtitle_streams,
        "attachment_streams": attachment_streams,
        "data_streams": data_streams,
        "duration": stream_duration_seconds({}, probe.get("format")) or stream_duration_seconds(video_streams[0], probe.get("format")) or 0.0,
    }


def join_item_answers(base_answers: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ffmpeg": base_answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "ffprobe": base_answers.get("ffprobe") or shutil.which("ffprobe") or "ffprobe",
        "detect_duplicate_audio": base_answers.get("detect_duplicate_audio", True),
        "input_path": item["path"],
        "probe": item.get("probe") or {},
        "format": item.get("format") or {},
        "video_streams": item.get("video_streams") or [],
        "audio_streams": item.get("audio_streams") or [],
        "subtitle_streams": item.get("subtitle_streams") or [],
        "attachment_streams": item.get("attachment_streams") or [],
        "data_streams": item.get("data_streams") or [],
    }


def join_stream_signature(item: dict[str, Any]) -> list[tuple[Any, ...]]:
    signature: list[tuple[Any, ...]] = []
    for stream in item.get("streams") or []:
        codec_type = stream.get("codec_type")
        if codec_type not in {"video", "audio", "subtitle"}:
            continue
        if codec_type == "video":
            signature.append(
                (
                    "video",
                    str(stream.get("codec_name") or "").lower(),
                    int(stream.get("width") or 0),
                    int(stream.get("height") or 0),
                    round(rational_to_float(stream.get("avg_frame_rate")) or rational_to_float(stream.get("r_frame_rate")) or 0.0, 3),
                    str(stream.get("pix_fmt") or "").lower(),
                )
            )
        elif codec_type == "audio":
            signature.append(
                (
                    "audio",
                    str(stream.get("codec_name") or "").lower(),
                    int(stream.get("sample_rate") or 0),
                    int(stream.get("channels") or 0),
                    str(stream.get("channel_layout") or "").lower(),
                )
            )
        else:
            signature.append(("subtitle", str(stream.get("codec_name") or "").lower()))
    return signature


def join_copy_compatibility(items: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    if len(items) < 2:
        return False, ["at least two video inputs are required"]
    reasons: list[str] = []
    first_ext = items[0]["path"].suffix.lower()
    first_sig = join_stream_signature(items[0])
    for item in items[1:]:
        if item["path"].suffix.lower() != first_ext:
            reasons.append("input containers/extensions differ")
            break
        if join_stream_signature(item) != first_sig:
            reasons.append("stream layout, codec, resolution, fps, pixel format, or audio layout differs")
            break
    return not reasons, reasons


def join_default_output_path(answers: dict[str, Any], first_input: Path) -> Path:
    output_location = Path(answers.get("output_location") or first_input.parent)
    suffix = first_input.suffix or ".mkv"
    if answers.get("output_name_stem"):
        candidate = output_location / f"{sanitize_output_stem(answers['output_name_stem'])}{suffix}"
    elif output_location.suffix:
        candidate = output_location.with_suffix(suffix)
    else:
        candidate = output_location / f"{sanitize_output_stem(first_input.stem)}_Joined{suffix}"
    return resolve_output_collision(candidate, first_input, "_Joined")


def write_join_concat_list(items: list[dict[str, Any]], output_path: Path) -> Path:
    list_path = unique_numbered_path(output_path.with_name(f".{sanitize_output_stem(output_path.stem)}_ffconcat.txt"))
    lines = ["ffconcat version 1.0"]
    for item in items:
        lines.append(f"file '{ffconcat_quote_path(item['path'])}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def build_join_copy_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Encode"),
    )
    answers["output_path"] = output_path
    list_path = write_join_concat_list(items, output_path)
    answers["_join_concat_list"] = list_path
    return [
        answers["ffmpeg"],
        "-hide_banner",
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-map",
        "0",
        "-c",
        "copy",
        str(output_path),
    ]


def cleanup_join_concat_list(answers: dict[str, Any]) -> None:
    list_path = answers.pop("_join_concat_list", None)
    if not list_path:
        return
    try:
        path = Path(list_path)
        if path.exists() and path.is_file():
            path.unlink()
            log_debug(f"Removed temporary join concat list: {path}")
    except Exception:
        log_exception("Could not remove temporary join concat list")


def cleanup_encode_chapter_metadata(answers: dict[str, Any]) -> None:
    """Remove the temporary directory used for encode chapter metadata files."""
    temp_dir = answers.pop("_chapter_metadata_temp_dir", None)
    answers.pop("_chapter_metadata_input_index", None)
    if not temp_dir:
        return
    try:
        dir_path = Path(temp_dir)
        if dir_path.exists() and dir_path.is_dir():
            shutil.rmtree(dir_path, ignore_errors=True)
            log_debug(f"Removed temporary chapter metadata directory: {dir_path}")
    except Exception:
        log_exception("Could not remove temporary chapter metadata directory")


def build_join_near_quality_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Final"),
    )
    answers["output_path"] = output_path
    first_video = items[0]["video_streams"][0]
    format_answers = dict(answers)
    format_answers["video_streams"] = [first_video]
    output_pix_fmt = cpu_pixel_format_for_output(format_answers)
    nvenc_pix_fmt = "p010le" if output_video_bit_depth(format_answers) > 8 else "yuv420p"
    target_depth = output_video_bit_depth(format_answers)
    target_w = int(first_video.get("width") or 1280)
    target_h = int(first_video.get("height") or 720)
    target_fps = rational_to_float(first_video.get("avg_frame_rate")) or rational_to_float(first_video.get("r_frame_rate")) or 30.0
    available_video_encoders = {str(name).lower() for name in answers.get("video_encoders") or []}
    use_nvenc_encode = "h264_nvenc" in available_video_encoders and target_depth <= 10
    use_cuda_decode_complex = bool(answers.get("use_gpu") and use_nvenc_encode)
    cmd: list[str] = [answers["ffmpeg"], "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n"]
    for item in items:
        if use_cuda_decode_complex:
            append_cuda_decode_args_for_input(cmd, answers)
        cmd.extend(["-i", str(item["path"])])
    filters: list[str] = []
    inputs: list[str] = []
    any_audio = any(item.get("audio_streams") for item in items)
    for idx, item in enumerate(items):
        filters.append(
            f"[{idx}:v:0]fps={target_fps:g},"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:reset_sar=1,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,"
            f"format={output_pix_fmt},setpts=PTS-STARTPTS[v{idx}]"
        )
        inputs.append(f"[v{idx}]")
        if any_audio and item.get("audio_streams"):
            filters.append(f"[{idx}:a:0]aresample=48000:async=1:first_pts=0,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{idx}]")
            inputs.append(f"[a{idx}]")
        elif any_audio:
            duration = max(0.001, float(item.get("duration") or 0.001))
            filters.append(f"anullsrc=channel_layout=stereo:sample_rate=48000:d={duration:.6f}[a{idx}]")
            inputs.append(f"[a{idx}]")
    filters.append(f"{''.join(inputs)}concat=n={len(items)}:v=1:a={1 if any_audio else 0}[v]{'[a]' if any_audio else ''}")
    cmd.extend(["-filter_complex", ";".join(filters), "-map", "[v]"])
    if any_audio:
        cmd.extend(["-map", "[a]"])
    else:
        cmd.append("-an")
    if use_nvenc_encode:
        log_info("Join Videos near-quality encode selected h264_nvenc because NVENC is available.")
        cmd.extend([
            "-c:v", "h264_nvenc",
            "-preset", NVENC_PRESET,
            "-tune", NVENC_TUNE,
            "-rc", "constqp",
        ])
        append_nvenc_multipass_args(cmd, answers, "h264_nvenc")
        cmd.extend([
            "-qp", "18",
            "-pix_fmt", nvenc_pix_fmt,
        ])
    elif target_depth > 10:
        cmd.extend(["-c:v", "libx265", "-preset", "slow", "-crf", "18", "-pix_fmt", output_pix_fmt])
        cmd.extend(["-profile:v", hevc_profile_for_output(format_answers, "main")])
    else:
        cmd.extend(["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", output_pix_fmt])
    if any_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ac", "2"])
    if output_path.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(str(output_path))
    return cmd


def append_join_trim_concat_filter(
    filters: list[str],
    input_label: str,
    keep_ranges: list[tuple[float, float]],
    media_type: str,
    output_label: str,
) -> str:
    if not keep_ranges:
        return input_label
    labels: list[str] = []
    trim_name = "trim" if media_type == "video" else "atrim"
    pts_filter = "setpts=PTS-STARTPTS" if media_type == "video" else "asetpts=PTS-STARTPTS"
    source_labels: list[str]
    if len(keep_ranges) > 1:
        split_name = "split" if media_type == "video" else "asplit"
        source_labels = [f"{output_label}_src{idx}" for idx in range(len(keep_ranges))]
        filters.append(f"[{input_label}]{split_name}={len(keep_ranges)}{''.join(f'[{label}]' for label in source_labels)}")
        log_info(
            f"Filter graph decision: inserted {split_name}={len(keep_ranges)} before multi-range "
            f"{trim_name} on [{input_label}] to avoid reusing one filter output."
        )
    else:
        source_labels = [input_label]
    for idx, (start, end) in enumerate(keep_ranges):
        label = f"{output_label}_{idx}"
        filters.append(f"[{source_labels[idx]}]{trim_name}=start={start:.6f}:end={end:.6f},{pts_filter}[{label}]")
        labels.append(f"[{label}]")
    if len(labels) == 1:
        return f"{output_label}_0"
    if media_type == "video":
        filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[{output_label}]")
    else:
        filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[{output_label}]")
    return output_label


def build_join_encode_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Encode"),
    )
    answers["output_path"] = output_path
    join_answers = dict(answers)
    join_answers["_join_complex_graph"] = True
    video_encoder, tag, profile = resolve_video_encoder(join_answers)
    if video_encoder == "copy":
        join_answers["video_codec"] = DEFAULT_VIDEO_CODEC
        video_encoder, tag, profile = resolve_video_encoder(join_answers)
    video_encoder, tag, profile = enforce_bit_depth_compatible_video_encoder(join_answers, video_encoder, tag, profile)
    first_video = items[0]["video_streams"][0]
    join_answers["video_streams"] = [first_video]
    target_dimensions = resolve_scale_dimensions(join_answers, join_answers.get("resolution", "n"))
    if target_dimensions:
        target_w, target_h = target_dimensions
    else:
        target_w = int(first_video.get("width") or 1280)
        target_h = int(first_video.get("height") or 720)
        join_answers["final_resolution"] = (target_w, target_h)
    target_fps = float(join_answers.get("fps") or rational_to_float(first_video.get("avg_frame_rate")) or rational_to_float(first_video.get("r_frame_rate")) or 30.0)

    cmd: list[str] = [join_answers["ffmpeg"], "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n"]
    use_cuda_decode_complex = should_use_cuda_decode_for_complex_graph(join_answers, video_encoder, False)
    for item in items:
        if use_cuda_decode_complex:
            append_cuda_decode_args_for_input(cmd, join_answers)
        cmd.extend(["-i", str(item["path"])])

    selected_audio = selected_audio_streams(join_answers) if join_answers.get("audio_streams") else []
    for item_pos, item in enumerate(items, start=1):
        audio_count = len(item.get("audio_streams") or [])
        missing = [idx for idx in selected_audio if idx >= audio_count]
        if missing:
            raise RuntimeError(
                f"Joined input {item_pos} has {audio_count} audio track(s), so selected track(s) {missing} cannot be mapped."
            )

    filters: list[str] = []
    concat_inputs: list[str] = []
    top = int(join_answers.get("crop_top", 0) or 0)
    left = int(join_answers.get("crop_left", 0) or 0)
    right = int(join_answers.get("crop_right", 0) or 0)
    bottom = int(join_answers.get("crop_bottom", 0) or 0)
    crop_filter = f"crop=iw-{left}-{right}:ih-{top}-{bottom}:{left}:{top}:exact=1" if join_answers.get("crop_enabled") and any((top, left, right, bottom)) else ""
    output_pix_fmt = cpu_pixel_format_for_output(join_answers)

    for input_idx, _item in enumerate(items):
        chain = []
        if crop_filter:
            chain.append(crop_filter)
        chain.extend([
            f"fps={target_fps:g}",
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:reset_sar=1",
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2",
            output_pix_fmt and f"format={output_pix_fmt}",
            "setpts=PTS-STARTPTS",
        ])
        chain = [part for part in chain if part]
        filters.append(f"[{input_idx}:v:0]{','.join(chain)}[jv{input_idx}]")
        concat_inputs.append(f"[jv{input_idx}]")
        for audio_pos, audio_index in enumerate(selected_audio):
            filters.append(
                f"[{input_idx}:a:{audio_index}]"
                f"aresample=48000:async=1:first_pts=0,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[ja{input_idx}_{audio_pos}]"
            )
            concat_inputs.append(f"[ja{input_idx}_{audio_pos}]")

    concat_outputs = ["[jvcat]"] + [f"[jacat{pos}]" for pos, _idx in enumerate(selected_audio)]
    filters.append(
        f"{''.join(concat_inputs)}concat=n={len(items)}:v=1:a={len(selected_audio)}{''.join(concat_outputs)}"
    )

    source_join_duration = sum(float(item.get("duration") or 0.0) for item in items)
    keep_ranges = normalize_cut_ranges(list(join_answers.get("cut_keep_ranges") or []), source_join_duration)
    video_label = append_join_trim_concat_filter(filters, "jvcat", keep_ranges, "video", "jvcut")
    if video_speed_transform_enabled(join_answers):
        filters.append(
            f"[{video_label}]{build_video_speed_filter(encode_video_speed_factor(join_answers), bool(join_answers.get('reverse_video')))}[jvfinal]"
        )
    else:
        filters.append(f"[{video_label}]setpts=PTS-STARTPTS[jvfinal]")
    video_label = "jvfinal"

    audio_labels: list[str] = []
    for audio_pos, _audio_index in enumerate(selected_audio):
        label = append_join_trim_concat_filter(filters, f"jacat{audio_pos}", keep_ranges, "audio", f"jacut{audio_pos}")
        final_audio_label = f"jafinal{audio_pos}"
        if audio_speed_transform_enabled(join_answers) or loudnorm_transform_enabled(join_answers):
            filters.append(f"[{label}]{build_encode_audio_speed_filter(join_answers)}[{final_audio_label}]")
        else:
            filters.append(f"[{label}]asetpts=PTS-STARTPTS[{final_audio_label}]")
        audio_labels.append(final_audio_label)

    final_duration = final_processed_duration_for_splits(join_answers, source_join_duration)
    split_points = normalize_separator_points(join_answers.get("separator_points"), final_duration)
    split_active = bool(split_points)
    if split_active:
        video_outputs, audio_outputs_by_part, split_intervals = append_final_split_filters(
            filters,
            video_label,
            audio_labels,
            split_points,
            final_duration,
            "j",
            float(join_answers.get("fps") or 0.0),
        )
        output_paths = split_part_output_paths(output_path, len(video_outputs), [Path(item["path"]) for item in items])
        join_answers["split_output_paths"] = output_paths
        join_answers["split_part_intervals"] = split_intervals
        answers["split_output_paths"] = output_paths
        answers["split_part_intervals"] = split_intervals
        join_answers["output_path"] = output_paths[0]
        answers["output_path"] = output_paths[0]
    else:
        video_outputs = [video_label]
        audio_outputs_by_part = [[label for label in audio_labels]]
        split_intervals = []
        output_paths = [output_path]

    cmd.extend(["-filter_complex", ";".join(filters)])

    if join_answers.get("use_gpu") and str(video_encoder).endswith("_nvenc"):
        log_info("Join Videos uses CPU concat filters; NVENC is still used for final video encoding.")
    for part_idx, part_output in enumerate(output_paths):
        cmd.extend(["-map", f"[{video_outputs[part_idx]}]"])
        for audio_label in audio_outputs_by_part[part_idx]:
            cmd.extend(["-map", f"[{audio_label}]"])
        attachments_mapped = append_embedded_attachment_maps(cmd, join_answers)
        data_mapped = append_source_data_maps(cmd, join_answers)
        append_source_metadata_chapter_options(cmd, join_answers)
        append_negative_stream_options(cmd, join_answers, True, [], data_mapped)
        append_video_encode_options(cmd, join_answers, video_encoder, tag, profile)
        append_audio_encode_options(cmd, join_answers, bool(audio_outputs_by_part[part_idx]))
        append_clear_reencoded_stream_stat_metadata(
            cmd,
            join_answers,
            video_output_count=1 if video_encoder != "copy" else 0,
            audio_output_count=len(audio_outputs_by_part[part_idx]) if audio_outputs_by_part[part_idx] else 0,
            subtitle_output_count=0,
        )
        if attachments_mapped:
            append_embedded_attachment_codec_options(cmd, join_answers)
        if data_mapped:
            append_source_data_codec_options(cmd, join_answers)
        append_container_options(cmd, join_answers["output_ext"])
        cmd.append(str(part_output))
    if split_active:
        log_info(
            "Split final joined output into parts: "
            + ", ".join(
                f"Part {idx + 1:02d} {seconds_to_ffmpeg_time(start)}->{seconds_to_ffmpeg_time(end)}"
                for idx, (start, end) in enumerate(split_intervals)
            )
        )
    answers["final_resolution"] = join_answers.get("final_resolution")
    answers.pop("_join_complex_graph", None)
    return cmd


def print_join_summary(items: list[dict[str, Any]], copy_compatible: bool, reasons: list[str]) -> None:
    print()
    print(paint("Join Videos summary:", Color.BOLD + Color.LIGHT_BLUE))
    for idx, item in enumerate(items, start=1):
        video = item["video_streams"][0]
        fps = rational_to_float(video.get("avg_frame_rate")) or rational_to_float(video.get("r_frame_rate")) or 0.0
        print(
            "  "
            + field_text(
                f"input {idx}",
                f"{item['path'].name} | duration: {format_duration(item.get('duration'))} | "
                f"video: {video.get('codec_name', 'unknown')} {video.get('width', '?')}x{video.get('height', '?')} {fps:g} fps | "
                f"audio tracks: {len(item.get('audio_streams') or [])}",
                Color.WHITE,
            )
        )
    if copy_compatible:
        print("  " + field_text("join mode", "stream copy, no re-encode", Color.GREEN))
    else:
        print("  " + field_text("join mode", "re-encode required", Color.ORANGE))
        for reason in reasons:
            print("    " + paint(reason, Color.YELLOW))


def run_join_videos_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    items: list[dict[str, Any]] = []
    try:
        while True:
            label = "Enter first video file path" if not items else "Enter another video file path"
            value = ask_required(
                question_prompt(
                    answers,
                    label,
                    "drag and drop a video file here or paste a path",
                )
            )
            path = terminal_path(value)
            if not path.exists() or not path.is_file():
                error("File not found. Enter the full file path again.")
                continue
            try:
                item = join_load_media_item(answers, path)
            except Exception as exc:
                log_exception(f"Join Videos probe failed: {path}")
                error(str(exc))
                continue
            items.append(item)
            answers["_question_number"] = len(items) + 1
            if len(items) >= 2:
                more = ask_yes_no(
                    question_prompt(answers, "Add another video file?", "y/n", "n"),
                    False,
                )
                answers["_question_number"] += 1
                if not more:
                    break

        output_answers = dict(answers)
        output_answers["input_path"] = items[0]["path"]
        output_answers["probe"] = items[0]["probe"]
        output_answers["format"] = items[0]["format"]
        output_answers["video_streams"] = items[0]["video_streams"]
        output_answers["audio_streams"] = items[0]["audio_streams"]
        output_answers["subtitle_streams"] = [stream for stream in items[0].get("streams", []) if stream.get("codec_type") == "subtitle"]
        output_answers["attachment_streams"] = [stream for stream in items[0].get("streams", []) if stream.get("codec_type") == "attachment"]
        output_answers["data_streams"] = [stream for stream in items[0].get("streams", []) if stream.get("codec_type") == "data"]
        output_answers["join_input_items"] = items[1:]
        step_output_location(output_answers)
        answers.update({key: output_answers[key] for key in ("output_location", "output_name_stem", "output_used_default") if key in output_answers})
    except Back:
        note("Returning to main menu.")
        return None

    output_path = join_default_output_path(answers, items[0]["path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    copy_compatible, reasons = join_copy_compatibility(items)
    print_join_summary(items, copy_compatible, reasons)
    if copy_compatible:
        cmd = build_join_copy_command(answers, items, output_path)
    else:
        note("These files cannot be safely joined with stream copy. Re-encoding is required.")
        use_near = ask_yes_no(
            question_prompt(
                answers,
                "Encode with closest possible quality to the inputs?",
                "y/n",
                "y",
            ),
            True,
        )
        if not use_near:
            note("Join Videos was canceled before encoding.")
            return None
        first_video = items[0]["video_streams"][0]
        format_answers = dict(answers)
        format_answers["video_streams"] = [first_video]
        target_depth = output_video_bit_depth(format_answers)
        available_video_encoders = {str(name).lower() for name in answers.get("video_encoders") or []}
        if "h264_nvenc" in available_video_encoders and target_depth <= 10:
            ask_nvenc_multipass_if_applicable(
                answers,
                video_encoder="h264_nvenc",
                workflow_name="Join Videos near-quality",
                quality_oriented=True,
            )
        else:
            set_nvenc_multipass_skip_reason(answers, "CPU encoder selected")
        cmd = build_join_near_quality_command(answers, items, output_path)
    output_path = Path(answers.get("output_path") or output_path)
    answers["output_path"] = output_path
    log_info(f"Join Videos command: {command_to_powershell(cmd)}")
    print()
    print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    start_now = ask_yes_no(question_prompt(answers, "Start FFmpeg now?", "y/n", "y"), True)
    if not start_now:
        note("FFmpeg was not started. The command above is ready to run manually.")
        cleanup_join_concat_list(answers)
        return None
    total_duration = sum(float(item.get("duration") or 0.0) for item in items)
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    try:
        return run_ffmpeg_with_progress(cmd, total_duration=(total_duration if total_duration > 0 else None), label="Join Videos")
    finally:
        cleanup_join_concat_list(answers)


def run_one_job(base_answers: dict[str, Any], config_path: Path) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    start_mode = ask_main_menu(answers, config_path)
    if start_mode == 13:
        return run_metadata_editor_mode(base_answers)
    if start_mode == 12:
        return run_join_videos_mode(base_answers)
    if start_mode == 11:
        return run_audio_transform_mode(base_answers)
    if start_mode == 10:
        return run_video_speed_reverse_mode(base_answers)
    if start_mode == 9:
        return run_hardsub_encode_mode(base_answers)
    if start_mode == 8:
        return run_mux_cleanup_mode(base_answers)
    if start_mode == 7:
        run_media_info_mode(base_answers)
        return None
    if start_mode == 6:
        return run_extract_stream_mode(base_answers)
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
        cleanup_join_concat_list(answers)
        cleanup_encode_chapter_metadata(answers)
        return None

    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    # Estimate total duration so the progress bar can compute percent / ETA.
    source_duration = stream_duration_seconds({}, answers.get("format")) or 0.0
    if answers.get("join_input_items"):
        source_duration += sum(float(item.get("duration") or 0.0) for item in answers.get("join_input_items") or [])
    processed_duration = final_processed_duration_for_splits(answers, source_duration) if source_duration > 0 else 0.0
    progress_duration = ffmpeg_progress_duration_for_answers(answers, source_duration) if source_duration > 0 else 0.0
    print_ffmpeg_processing_plan(
        answers,
        cmd,
        progress_duration if progress_duration > 0 else None,
        processed_duration if processed_duration > 0 else None,
    )
    log_info(f"Starting FFmpeg encode. Estimated source duration: "
             f"{format_elapsed(source_duration) if source_duration else 'unknown'}; "
             f"estimated processed duration: {format_elapsed(processed_duration) if processed_duration else 'unknown'}; "
             f"progress duration: {format_elapsed(progress_duration) if progress_duration else 'unknown'}")
    if reverse_video_needs_segmented_main_encode(answers) and not answers.get("separator_points"):
        return run_segmented_reverse_main_encode(answers)
    try:
        split_progress_fps = None
        if answers.get("separator_points") and progress_duration > 0:
            try:
                split_progress_fps = float(answers.get("fps") or get_video_fps(answers))
            except Exception:
                split_progress_fps = None
        split_part_intervals = list(answers.get("split_part_intervals") or [])
        split_progress_part_durations = [
            max(0.0, float(end) - float(start))
            for start, end in split_part_intervals
        ]
        progress_output_paths = [Path(path) for path in (answers.get("split_output_paths") or [])]
        if not progress_output_paths and answers.get("output_path"):
            progress_output_paths = [Path(answers["output_path"])]
        if cpu_two_pass_enabled_for_command(answers, cmd):
            return run_cpu_two_pass_ffmpeg(
                cmd,
                answers,
                total_duration=(progress_duration if progress_duration > 0 else None),
                progress_output_paths=progress_output_paths,
            )
        return_code, elapsed = run_ffmpeg_with_progress(
            cmd, total_duration=(progress_duration if progress_duration > 0 else None),
            label="FFmpeg encode",
            split_progress_fps=split_progress_fps,
            split_progress_part_durations=split_progress_part_durations,
            initial_detail=ffmpeg_initial_progress_detail(answers, cmd),
            progress_output_paths=progress_output_paths,
        )
    finally:
        cleanup_join_concat_list(answers)
        cleanup_encode_chapter_metadata(answers)
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

    # Auto-install PySide6 (the runtime for the active unified/speed/audio
    # GUI windows) on first run so graphical editors are available. This
    # block runs only when PySide6 is missing; subsequent runs detect it
    # via the cached probe and skip the prompt entirely.
    try:
        ensure_pyside6_installed(interactive=True)
    except Exception as exc:
        note(f"PySide6 auto-install check failed: {exc}")

    video_encoders = list_encoders(ffmpeg, "video")
    base_answers: dict[str, Any] = {
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "muxers": list_muxers(ffmpeg),
        "video_encoders": video_encoders,
        "audio_encoders": list_encoders(ffmpeg, "audio"),
        "gpu_available": detect_nvidia_gpu_available(ffmpeg, video_encoders),
        "detect_duplicate_audio": True,
    }
    # Resolve the GPU model name once (for the startup banner) so the per-job
    # banner reprint stays instant instead of re-querying nvidia-smi each loop.
    if base_answers["gpu_available"]:
        base_answers["gpu_model"] = detect_gpu_model_name(ffmpeg)
        log_info(f"GPU detected for encoding: {base_answers.get('gpu_model') or 'NVIDIA NVENC GPU'}")
    else:
        base_answers["gpu_model"] = None
        log_info("No usable NVENC GPU detected; CPU encoding will be used.")

    first_run = True
    while True:
        if not first_run:
            print()
            note("Ready for a new job.")
            print()
        print_startup_banner(config_path, launcher_path, base_answers)
        result = run_one_job(base_answers, config_path)
        if result is None:
            note("Returning to the first question.")
            first_run = False
            continue
        return_code, elapsed = result
        print()
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







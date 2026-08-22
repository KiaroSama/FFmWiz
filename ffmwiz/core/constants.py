"""FFmWiz core constants, default FFmpeg options, and configuration template.

Extracted verbatim from FFmWiz.py during the package decomposition.
Leaf module: depends only on the Python standard library (os).
FFmWiz.py re-exports every name here via `from ffmwiz.core.constants import *`,
so the public API (FFmWiz.<CONST>) is unchanged.
"""
from __future__ import annotations

import os


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

# SVT-AV1 (libsvtav1) defaults. SVT-AV1 is the recommended CPU AV1 encoder
# (much faster than libaom-av1 at comparable quality). preset is an INTEGER
# 0-13 (lower = slower/better); 6 is a widely recommended balance. tune=0
# targets subjective visual quality (tune=1 = PSNR).
SVTAV1_PRESET = "6"
SVTAV1_PARAMS = "tune=0"

# CPU encoders that support FFmpeg's -pass 1/2 two-pass rate control. Verified
# against `ffmpeg -h encoder=<name>` and by running a real 2-pass cycle:
# libx264/libx265 (-passlogfile/-x265-stats), libvpx-vp9 and libaom-av1
# ("2-pass only" options), libsvtav1 (SVT 2PASS RC), and generic mpeg4.
TWO_PASS_CPU_ENCODERS = {"libx264", "libx265", "libvpx-vp9", "libaom-av1", "libsvtav1", "mpeg4"}

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

# The Audio Cut / Speed / Reverse tools always re-encode, so they derive their
# target bitrate from the SOURCE instead of pinning every output to 128 kbps.
# The bounds keep a lossless source (PCM estimates ~1411 kbps) from becoming a
# nonsensical target for a lossy encoder, and keep a very low-rate source from
# being re-encoded into mush.
AUDIO_TOOL_MIN_BITRATE_KBPS = 64
AUDIO_TOOL_MAX_BITRATE_KBPS = 320
# Channel counts above this fall back to AUDIO_CHANNELS rather than being
# preserved, because not every target encoder handles exotic layouts.
MAX_PRESERVED_AUDIO_CHANNELS = 8

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

# Recognized output container/format extensions. A value outside this set is
# very likely a typo (e.g. "acc" for "aac") that would make FFmpeg fail with
# "Unable to find a suitable output format"; the wizard warns and suggests the
# closest match before accepting it.
KNOWN_OUTPUT_FORMATS = {
    "mp4", "mkv", "mov", "webm", "avi", "m4v", "ts", "mpg", "mpeg", "wmv", "flv",
    "ogv", "3gp", "mts", "m2ts", "vob", "mxf",
    "mp3", "m4a", "aac", "opus", "ogg", "oga", "wav", "flac", "ac3", "eac3",
    "wma", "alac", "aiff", "aif", "amr", "mka", "caf", "spx",
}
COMMON_VIDEO_CODECS = ["H265", "H264", "AV1", "VP9", "MPEG4", "copy"]
COMMON_AUDIO_CODECS = ["aac", "libopus", "opus", "libmp3lame", "flac", "pcm_s16le", "copy"]
# Common output audio sample rates (Hz) shown as prompt examples.
COMMON_AUDIO_SAMPLE_RATES = [44100, 48000, 96000]
MIN_AUDIO_SAMPLE_RATE = 8000
MAX_AUDIO_SAMPLE_RATE = 192000
CONFIG_FILE_NAME = "config.env"
CONFIG_EXAMPLE_FILE_NAME = "config.env.example"
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

# The Mode-2 config.env template is a large (~390 line) annotated string; it
# lives in a sibling module to keep this constants file scannable.
from ffmwiz.core.constants_config_template import CONFIG_TEMPLATE  # noqa: E402


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
    "av1": {"cpu": "libsvtav1", "gpu": "av1_nvenc", "tag": None, "profile": None},
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





import re  # for regex constants consolidated below



# --- Additional constants consolidated from FFmWiz.py (Layer 3) ---
JOIN_AUDIO_PREP_FILTER = "aresample=48000:async=1:first_pts=0,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS"

FFMWIZ_RUNTIME_DIR_NAME = "runtime"

# The bundled GUI (classic + QML) lives inside the package at ffmwiz/gui/.
FFMWIZ_GUI_DIR_NAME = "gui"

FFMWIZ_GUI_FILE_NAME = "ffmwiz_gui.py"

REQUIREMENTS_FILE_NAME = "requirements.txt"

PYSIDE6_DISPLAY_NAME = "PySide6"

PYSIDE6_PIP_SPEC = "PySide6==6.11.1"

LOGS_DIR_NAME = "Logs"

APP_VERSION = "1.3.0"

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

COLOR_RANGE_ALIASES = {
    "tv": "tv",
    "limited": "tv",
    "mpeg": "tv",
    "pc": "pc",
    "full": "pc",
    "jpeg": "pc",
}

CAPABILITY_CACHE_SCHEMA_VERSION = 1

CAPABILITY_CACHE_DIRNAME = ".cache"

CAPABILITY_CACHE_FILENAME = "ffmpeg_capabilities.json"

CAPABILITY_GROUP = "color_range_do_not_force"

SAR_DAR_MAX_DENOMINATOR = 1000

SAR_DAR_TOLERANCE = 0.01

VOLUMEDETECT_RE = re.compile(r"\b(mean_volume|max_volume):\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*dB")

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

BACK_INPUT_TOKENS = {"0", "۰", "٠"}

INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

JOIN_ADD_ANOTHER_BACK = "back=0, quit=exit, f=join all videos in folder"

JOIN_ADD_ANOTHER_BACK_AUDIO = "back=0, quit=exit"

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

COPY_CUT_WARNING = (
    "Stream-copy cutting is very fast and keeps original quality, but cut "
    "points may snap to nearby keyframes. For exact frame-accurate cutting, "
    "use re-encode mode."
)

# Codec/extension lookup tables live in a sibling module to keep this file
# under 800 lines.
from ffmwiz.core.constants_tables import *  # noqa: E402,F401,F403


__all__ = [
    'GPU_DEVICE_INDEX',
    'OVERWRITE_OUTPUT',
    'COLOR_RANGE',
    'SETPARAMS_RANGE',
    'CUDA_FORMAT',
    'CPU_FORMAT',
    'FORCE_SAR',
    'MOVFLAGS',
    'NVENC_PRESET',
    'NVENC_TUNE',
    'NVENC_RC',
    'NVENC_HEVC_PROFILE',
    'CPU_PRESET',
    'SVTAV1_PRESET',
    'SVTAV1_PARAMS',
    'TWO_PASS_CPU_ENCODERS',
    'DEFAULT_AUDIO_CODEC',
    'DEFAULT_AUDIO_BITRATE_KBPS',
    'AUDIO_CHANNELS',
    'AUDIO_SAMPLE_RATE',
    'LOUDNORM_DEFAULT_TARGET_I',
    'LOUDNORM_TARGET_TP',
    'LOUDNORM_TARGET_LRA',
    'LOUDNORM_MIN_TARGET_I',
    'LOUDNORM_MAX_TARGET_I',
    'DEFAULT_SPEED_FACTOR',
    'MIN_SPEED_FACTOR',
    'MAX_SPEED_FACTOR',
    'DEFAULT_SPEED_AUDIO_BITRATE_KBPS',
    'AUDIO_TOOL_MIN_BITRATE_KBPS',
    'AUDIO_TOOL_MAX_BITRATE_KBPS',
    'MAX_PRESERVED_AUDIO_CHANNELS',
    'REVERSE_SEGMENT_SECONDS',
    'DEFAULT_VIDEO_CODEC',
    'DEFAULT_OUTPUT_VIDEO_BITRATE_KBPS',
    'COMMON_VIDEO_FORMATS',
    'COMMON_AUDIO_FORMATS',
    'KNOWN_OUTPUT_FORMATS',
    'COMMON_VIDEO_CODECS',
    'COMMON_AUDIO_CODECS',
    'COMMON_AUDIO_SAMPLE_RATES',
    'MIN_AUDIO_SAMPLE_RATE',
    'MAX_AUDIO_SAMPLE_RATE',
    'CONFIG_FILE_NAME',
    'CONFIG_EXAMPLE_FILE_NAME',
    'LAUNCHER_FILE_NAME',
    'ASSET_DIR_NAME',
    'ICON_DIR_NAME',
    'CURSOR_DIR_NAME',
    'DEFAULT_OUTPUT_LOCATION_TEXT',
    'env_int',
    'env_float',
    'EMPTY_AUDIO_MAX_BYTES',
    'NEAR_EMPTY_AUDIO_MAX_BYTES',
    'NEAR_EMPTY_AUDIO_RATIO',
    'NEAR_EMPTY_AUDIO_MAX_KBPS',
    'PACKET_SIZE_PROBE_MAX_MB',
    'PACKET_SIZE_PROBE_MAX_BYTES',
    'DUPLICATE_AUDIO_HASH_SECONDS',
    'DUPLICATE_AUDIO_HASH_WORKERS',
    'VOLUME_SCAN_WORKERS',
    'FOLDER_PROBE_WORKERS',
    'AUDIO_CODEC_DEFAULTS_BY_FORMAT',
    'STREAM_STAT_METADATA_TAGS',
    'AUDIO_CODEC_ALIASES',
    'BITRATE_AUDIO_CODECS',
    'TEXT_SUBTITLE_CODECS',
    'BITMAP_SUBTITLE_CODECS',
    'HARDSUB_BITMAP_SUBTITLE_ERROR',
    'FFMPEG_REFERENCE_FILE_NAME',
    'CONFIG_TEMPLATE',
    'AUDIO_ONLY_EXTS',
    'FOLDER_VIDEO_EXTS',
    'FOLDER_MEDIA_EXTS',
    'MP4_LIKE_EXTS',
    'ATTACHMENT_COMPATIBLE_EXTS',
    'ADD_FILES_OUTPUT_SUFFIX',
    'EXTRACT_STREAM_OUTPUT_SUFFIX',
    'MEDIA_REPORTS_DIR_NAME',
    'MUX_CLEANUP_VIDEO_EXTS',
    'ROBOCOPY_BIN',
    'HARDSUB_OUTPUT_SUFFIX',
    'GENERATED_OUTPUT_SUFFIXES',
    'HARDSUB_SUBTITLE_EXTS',
    'HARDSUB_QUALITY_PRESETS',
    'RESOLUTION_PRESETS',
    'VIDEO_CODEC_ALIASES',
    'NVENC_MULTIPASS_MODES',
    'CUDA_CUVID_DECODER_BY_CODEC',
    'JOIN_AUDIO_PREP_FILTER',
    'FFMWIZ_RUNTIME_DIR_NAME',
    'FFMWIZ_GUI_DIR_NAME',
    'FFMWIZ_GUI_FILE_NAME',
    'REQUIREMENTS_FILE_NAME',
    'PYSIDE6_DISPLAY_NAME',
    'PYSIDE6_PIP_SPEC',
    'LOGS_DIR_NAME',
    'APP_VERSION',
    'ANSI_ESCAPE_RE',
    'COLOR_RANGE_ALIASES',
    'CAPABILITY_CACHE_SCHEMA_VERSION',
    'CAPABILITY_CACHE_DIRNAME',
    'CAPABILITY_CACHE_FILENAME',
    'CAPABILITY_GROUP',
    'SAR_DAR_MAX_DENOMINATOR',
    'SAR_DAR_TOLERANCE',
    'VOLUMEDETECT_RE',
    'FOLDER_MEDIA_METADATA_KEYS',
    'BACK_INPUT_TOKENS',
    'INVALID_FILENAME_CHARS_RE',
    'JOIN_ADD_ANOTHER_BACK',
    'JOIN_ADD_ANOTHER_BACK_AUDIO',
    'METADATA_DISPOSITION_FLAGS',
    'COPY_CUT_WARNING',
    'LOSSLESS_AUDIO_COPY_EXT_BY_CODEC',
    'LOSSLESS_AUDIO_COPY_EXT_CHOICES',
    'TRACK_MANAGER_MEDIA_EXTS',
    'EXTRACT_AUDIO_EXTENSIONS',
    'EXTRACT_SUBTITLE_EXTENSIONS',
    'EXTRACT_COPY_CONTAINERS_AUDIO',
    'EXTRACT_COPY_CONTAINERS_VIDEO',
    'EXTRACT_COPY_CONTAINERS_SUBTITLE',
]

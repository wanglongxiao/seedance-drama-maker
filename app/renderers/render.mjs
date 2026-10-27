import {bundle} from '@remotion/bundler';
import {selectComposition, renderMedia} from '@remotion/renderer';
import {serializeSrt, createTikTokStyleCaptions} from '@remotion/captions';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
const root = path.dirname(fileURLToPath(import.meta.url));
const [propsFile, output, srt] = process.argv.slice(2);
const inputProps = JSON.parse(fs.readFileSync(propsFile, 'utf8'));
const pages = createTikTokStyleCaptions({captions: inputProps.captions, combineTokensWithinMilliseconds: 1200}).pages;
fs.writeFileSync(srt, serializeSrt({lines: pages.map(p => p.tokens.map(t => ({
  text: t.text, startMs: t.fromMs, endMs: t.toMs, timestampMs: t.fromMs, confidence: 1
})))}));
if (inputProps.subtitleRenderer === 'ffmpeg') {
  // Static pages need no browser video decoding or per-frame React rendering.
  // Keep Remotion's page grouping and SRT timestamps identical in both paths.
  const {width, height} = inputProps;
  const stamp = (ms) => {
    const cs = Math.round(ms / 10);
    return `${Math.floor(cs / 360000)}:${String(Math.floor(cs / 6000) % 60).padStart(2, '0')}:${String(Math.floor(cs / 100) % 60).padStart(2, '0')}.${String(cs % 100).padStart(2, '0')}`;
  };
  const plainText = (text) => text.trim()
    .replace(/\\/g, '\\\uFEFF').replace(/{/g, '\\{').replace(/}/g, '\\}')
    .replace(/\r?\n/g, '\\N');
  const margin = Math.round(width * .05);
  const events = pages.map((page, index) => {
    const end = Math.min(Math.max(...page.tokens.map(t => t.toMs)),
      pages[index + 1]?.startMs ?? Infinity);
    if (end <= page.startMs) return '';
    return `Dialogue: 0,${stamp(page.startMs)},${stamp(end)},Default,,0,0,0,,${plainText(page.text)}`;
  }).filter(Boolean);
  const ass = `[Script Info]
ScriptType: v4.00+
PlayResX: ${width}
PlayResY: ${height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,${Math.round(height * .05)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,1.5,1,2,${margin},${margin},${Math.round(height * .065)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
${events.join('\n')}
`;
  const directory = path.dirname(path.resolve(propsFile));
  fs.writeFileSync(path.join(directory, 'subtitles.ass'), ass);
  // Use a fixed relative filter filename: project paths never enter filter syntax.
  execFileSync('ffmpeg', ['-v', 'error', '-y',
    '-i', path.join(root, 'public', inputProps.source), '-map', '0:v:0', '-an',
    ...(events.length ? ['-vf', 'ass=subtitles.ass', '-fps_mode', 'passthrough',
      '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p']
      : ['-c:v', 'copy']),
    '-movflags', '+faststart', path.resolve(output)], {cwd: directory});
} else if (inputProps.subtitleRenderer === 'remotion' || !inputProps.subtitleRenderer) {
  const serveUrl = await bundle({entryPoint: path.join(root, 'captions.tsx'), publicDir: path.join(root, 'public')});
  const browserExecutable = process.env.REMOTION_BROWSER_EXECUTABLE || undefined;
  const composition = await selectComposition({serveUrl, id: 'SubtitledFilm', inputProps, browserExecutable});
  await renderMedia({serveUrl, composition, inputProps, outputLocation: output, codec: 'h264',
    audioCodec: 'aac', crf: 18, concurrency: 2, browserExecutable});
} else {
  throw new Error('Unsupported subtitle renderer');
}

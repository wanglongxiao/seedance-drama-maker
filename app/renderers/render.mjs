import {bundle} from '@remotion/bundler';
import {selectComposition, renderMedia} from '@remotion/renderer';
import {serializeSrt, createTikTokStyleCaptions} from '@remotion/captions';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root = path.dirname(fileURLToPath(import.meta.url));
const [propsFile, output, srt] = process.argv.slice(2);
const inputProps = JSON.parse(fs.readFileSync(propsFile, 'utf8'));
const pages = createTikTokStyleCaptions({captions: inputProps.captions, combineTokensWithinMilliseconds: 1200}).pages;
fs.writeFileSync(srt, serializeSrt({lines: pages.map(p => p.tokens.map(t => ({
  text: t.text, startMs: t.fromMs, endMs: t.toMs, timestampMs: t.fromMs, confidence: 1
})))}));
const serveUrl = await bundle({entryPoint: path.join(root, 'captions.tsx'), publicDir: path.join(root, 'public')});
const browserExecutable = process.env.REMOTION_BROWSER_EXECUTABLE || undefined;
const composition = await selectComposition({serveUrl, id: 'SubtitledFilm', inputProps, browserExecutable});
await renderMedia({serveUrl, composition, inputProps, outputLocation: output, codec: 'h264',
  audioCodec: 'aac', crf: 18, concurrency: 2, browserExecutable});

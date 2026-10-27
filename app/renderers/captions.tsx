import React from 'react';
import {AbsoluteFill, Composition, OffthreadVideo, registerRoot, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {createTikTokStyleCaptions} from '@remotion/captions';

const Film = (props: any) => {
  const frame = useCurrentFrame();
  const {fps, height} = useVideoConfig();
  const pages = createTikTokStyleCaptions({captions: props.captions, combineTokensWithinMilliseconds: 1200}).pages;
  const ms = frame / fps * 1000;
  const page = pages.find((p: any) => ms >= p.startMs && ms < Math.max(...p.tokens.map((t: any) => t.toMs)));
  return <AbsoluteFill style={{backgroundColor: 'black'}}>
    <OffthreadVideo src={staticFile(props.source)} />
    {page && <div style={{position: 'absolute', bottom: height * .065, width: '90%', left: '5%',
      textAlign: 'center', fontFamily: 'Arial, PingFang SC, sans-serif', fontSize: Math.round(height * .05),
      lineHeight: 1.4, color: 'white', fontWeight: 600, overflowWrap: 'anywhere',
      textShadow: '0 2px 4px black, 2px 0 3px black, -2px 0 3px black'}}>
      {page.text.trim()}
    </div>}
  </AbsoluteFill>;
};
registerRoot(() => <Composition id="SubtitledFilm" component={Film} width={1280} height={720}
  fps={25} durationInFrames={25} defaultProps={{source: '', captions: []}}
  calculateMetadata={({props}: any) => ({width: props.width, height: props.height,
    fps: props.fps, durationInFrames: props.durationInFrames})} />);

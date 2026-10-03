import { describe, it, expect } from "vitest";
import { selectBestCaptionTrack, parseVtt, cuesToWords } from "./captions.js";

describe("selectBestCaptionTrack", () => {
  it("prefers manual subtitles over automatic captions", () => {
    const track = selectBestCaptionTrack(
      { en: [{ ext: "vtt", url: "manual-en" }] },
      { en: [{ ext: "vtt", url: "auto-en" }] },
    );
    expect(track).toEqual({ lang: "en", kind: "manual", ext: "vtt", url: "manual-en" });
  });

  it("prefers an -orig track over a plain language code (translated)", () => {
    const track = selectBestCaptionTrack(null, {
      ka: [{ ext: "vtt", url: "translated-ka" }],
      "ka-orig": [{ ext: "vtt", url: "original-ka" }],
      en: [{ ext: "vtt", url: "translated-en" }],
    });
    expect(track?.lang).toBe("ka-orig");
    expect(track?.url).toBe("original-ka");
  });

  it("prefers the vtt track when multiple formats exist for a language", () => {
    const track = selectBestCaptionTrack(null, {
      en: [
        { ext: "srv3", url: "srv3-url" },
        { ext: "vtt", url: "vtt-url" },
        { ext: "ttml", url: "ttml-url" },
      ],
    });
    expect(track?.url).toBe("vtt-url");
  });

  it("returns null when there are no tracks at all", () => {
    expect(selectBestCaptionTrack(null, null)).toBeNull();
    expect(selectBestCaptionTrack({}, {})).toBeNull();
  });
});

describe("parseVtt", () => {
  it("parses cues with timestamps and text", () => {
    const vtt = [
      "WEBVTT",
      "",
      "00:00:01.000 --> 00:00:04.000",
      "Hello world",
      "",
      "00:00:04.000 --> 00:00:07.500",
      "second line here",
      "",
    ].join("\n");
    const cues = parseVtt(vtt);
    expect(cues).toEqual([
      { start: 1, end: 4, text: "Hello world" },
      { start: 4, end: 7.5, text: "second line here" },
    ]);
  });

  it("strips inline word-highlight tags auto-captions add", () => {
    const vtt = [
      "WEBVTT",
      "",
      "00:00:01.000 --> 00:00:04.000 align:start position:0%",
      "<00:00:01.000><c> Hello</c><00:00:01.500><c> world</c>",
      "",
    ].join("\n");
    const cues = parseVtt(vtt);
    expect(cues).toEqual([{ start: 1, end: 4, text: "Hello world" }]);
  });

  it("handles an hour component in timestamps", () => {
    const vtt = ["WEBVTT", "", "01:00:01.000 --> 01:00:04.000", "late line", ""].join("\n");
    const cues = parseVtt(vtt);
    expect(cues[0].start).toBe(3601);
    expect(cues[0].end).toBe(3604);
  });

  it("returns no cues for text with no timestamp lines", () => {
    expect(parseVtt("WEBVTT\n\njust some text\n")).toEqual([]);
  });
});

describe("cuesToWords", () => {
  it("spaces words evenly across the cue, each word's end is the next word's start", () => {
    const words = cuesToWords([{ start: 0, end: 4, text: "one two three four" }]);
    expect(words).toEqual([
      { word: "one", start: 0, end: 1, confidence: 1 },
      { word: "two", start: 1, end: 2, confidence: 1 },
      { word: "three", start: 2, end: 3, confidence: 1 },
      { word: "four", start: 3, end: 4, confidence: 1 },
    ]);
  });

  it("caps the last word of a cue at the cue's own end rather than leaving it open", () => {
    const words = cuesToWords([{ start: 10, end: 11, text: "ok" }]);
    expect(words.at(-1)?.end).toBe(11);
  });

  it("drops a rolling auto-caption cue that is a prefix of the next one", () => {
    const words = cuesToWords([
      { start: 0, end: 1, text: "hello" },
      { start: 0, end: 2, text: "hello world" },
      { start: 2, end: 4, text: "next sentence" },
    ]);
    expect(words.map((w) => w.word)).toEqual(["hello", "world", "next", "sentence"]);
  });

  it("concatenates words across multiple distinct cues in order", () => {
    const words = cuesToWords([
      { start: 0, end: 2, text: "first cue" },
      { start: 2, end: 4, text: "second cue" },
    ]);
    expect(words.map((w) => w.word)).toEqual(["first", "cue", "second", "cue"]);
  });

  it("returns an empty array for no cues", () => {
    expect(cuesToWords([])).toEqual([]);
  });
});

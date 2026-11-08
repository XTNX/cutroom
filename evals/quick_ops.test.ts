// Quick-mode timeline operations: split, move (free / overwrite), trim/extend, delete (lift / ripple),
// linked picture+sound. Run: node evals/quick_ops.test.ts
import { closeGaps, copy, link, move, paste, remove, spliceFades, split, trim, unlink, type QDoc } from "../src/quickOps.ts";

let fails = 0;
const ok = (cond: boolean, what: string) => { console.log((cond ? "PASS " : "FAIL ") + what); if (!cond) fails++; };

const base = (): QDoc => ({
  version: 1, rate: "25/1", roles: {}, grade: null, music: null, presentation: null, meta: {},
  tracks: {
    video: [{ id: "v1", start: 0, len: 100, src: "A", in: 10 }, { id: "v2", start: 100, len: 50, src: "B", in: 0 }, { id: "v3", start: 200, len: 50, src: "A", in: 30 }],
    audio: [{ id: "a1", start: 0, len: 100, src: "L", in: 5, link: "v1" }, { id: "a2", start: 100, len: 50, src: "L", in: 9, link: "v2" }, { id: "a3", start: 200, len: 50, src: "L", in: 40, link: "v3" }],
    slides: [{ id: "s1", start: 90, len: 40, page: 1 }],
    overlays: [{ id: "o1", start: 210, len: 30 }],
  },
});
const dur = () => 1000;

// split at the playhead with nothing selected: every block under it, picture and sound stay paired
let d = split(base(), [], 40);
ok(d.tracks.video.length === 4 && d.tracks.video[1].start === 40 && d.tracks.video[1].in === 10 + 40 / 25, "разрез: блок делится, вход второй половины сдвинут на 1,6 с");
ok(d.tracks.audio[1].link === d.tracks.video[1].id, "разрез: звук второй половины связан со второй половиной картинки");

// free move stops at the neighbour
d = move(base(), [{ track: "video", id: "v3" }], -80, false);
ok(d.tracks.video.find((b) => b.id === "v3")!.start === 150, "перенос: блок упирается в соседа (150), не залезает на него");
ok(d.tracks.audio.find((b) => b.id === "a3")!.start === 150, "перенос: связанный звук едет вместе с картинкой");

// overwrite: lands on the neighbour and replaces what it covers
d = move(base(), [{ track: "video", id: "v3" }], -120, true);
const v1 = d.tracks.video.find((b) => b.id === "v1")!;
ok(d.tracks.video.find((b) => b.id === "v3")!.start === 80 && v1.len === 80, "перезапись: блок встал на 80, сосед обрезан до 80 кадров");
const tail = d.tracks.video.find((b) => b.id === "v2")!;
ok(tail.start === 130 && tail.len === 20 && Math.abs(tail.in - 1.2) < 1e-6, "перезапись: от соседа остался хвост 130–150 с верным входом (1,2 с)");
ok(d.tracks.audio.find((b) => b.id === "a2")!.start === 130, "перезапись: звук хвоста обрезан так же и связан с ним");
d = move(base(), [{ track: "video", id: "v3" }], -100, true);
ok(!d.tracks.video.some((b) => b.id === "v2") && !d.tracks.audio.some((b) => b.id === "a2"), "перезапись: полностью закрытый блок исчез вместе со звуком");

// trim / extend: media can't go before its source start, stops at neighbours
d = trim(base(), { track: "video", id: "v1" }, "start", -500, dur);
ok(d.tracks.video[0].start === 0 && d.tracks.video[0].in === 10, "растяжка влево: упирается в начало ролика");
d = trim(base(), { track: "video", id: "v2" }, "end", 500, dur);
ok(d.tracks.video.find((b) => b.id === "v2")!.len === 100, "растяжка вправо: доходит до соседа (100 кадров), не дальше");
d = trim(base(), { track: "video", id: "v2" }, "start", 20, dur);
const v2 = d.tracks.video.find((b) => b.id === "v2")!;
ok(v2.start === 120 && v2.len === 30 && Math.abs(v2.in - 0.8) < 1e-6, "подрезка слева: старт 120, длина 30, вход +0,8 с");
ok(d.tracks.audio.find((b) => b.id === "a2")!.start === 120, "подрезка: связанный звук подрезан так же");
d = trim(base(), { track: "video", id: "v2" }, "end", 80, dur, true);
ok(d.tracks.video.find((b) => b.id === "v2")!.len === 130 && d.tracks.video.find((b) => b.id === "v3")!.start === 230, "растяжка с перезаписью: съедает начало соседа");

// delete: lift leaves a gap, ripple closes it on every track
d = remove(base(), [{ track: "video", id: "v2" }], false);
ok(d.tracks.video.length === 2 && d.tracks.video[1].start === 200 && d.tracks.audio.length === 2, "удаление: блок и его звук убраны, пробел остался");
d = remove(base(), [{ track: "video", id: "v2" }], true);
ok(d.tracks.video[1].start === 150 && d.tracks.overlays[0].start === 160, "удаление со сдвигом: всё после сдвинулось на 50 кадров");
ok(d.tracks.slides[0].start === 90 && d.tracks.slides[0].len === 10 + 0 + 0 + 10 - 10, "удаление со сдвигом: слайд на стыке укоротился");
d = closeGaps(base());
ok(d.tracks.video[2].start === 150, "закрыть пробелы: пауза 150–200 убрана");

// linked both ways: dragging the SOUND's edge moves the picture's edge too (it used to change only the sound)
d = trim(base(), { track: "audio", id: "a3" }, "end", -20, dur);
ok(d.tracks.audio.find((b) => b.id === "a3")!.len === 30 && d.tracks.video.find((b) => b.id === "v3")!.len === 30, "растяжка звука: связанная картинка подрезана так же");
d = trim(base(), { track: "audio", id: "a3" }, "start", 10, dur);
const v3t = d.tracks.video.find((b) => b.id === "v3")!;
ok(v3t.start === 210 && Math.abs(v3t.in - (30 + 10 / 25)) < 1e-6, "растяжка звука за начало: картинка сдвинула вход так же (30,4 с)");
d = move(base(), [{ track: "audio", id: "a3" }], -30, false);
ok(d.tracks.video.find((b) => b.id === "v3")!.start === 170, "перенос звука: связанная картинка едет вместе");
d = unlink(base(), [{ track: "audio", id: "a3" }]);
ok(!d.tracks.audio.find((b) => b.id === "a3")!.link, "отвязать: связь звука с картинкой снята");
d = trim(d, { track: "audio", id: "a3" }, "end", -20, dur);
ok(d.tracks.video.find((b) => b.id === "v3")!.len === 50, "после отвязки звук растягивается один, картинка на месте");
ok(link(d, [{ track: "video", id: "v3" }, { track: "audio", id: "a3" }]).tracks.audio.find((b) => b.id === "a3")!.link === "v3", "связать: звук снова привязан");

// fades at the cuts: both sides of each touching pair, nothing at a free end
d = spliceFades(base(), [], 0.12);
const [fa1, fa2, fa3] = d.tracks.audio;
ok(fa1.fade_out === 0.12 && fa2.fade_in === 0.12 && !fa1.fade_in, "фейды на склейках: у стыка 100 — выход первого и вход второго");
ok(!fa2.fade_out && !fa3.fade_in, "фейды на склейках: где звук не стыкуется (150–200), фейдов нет");
ok(!spliceFades(d, [], 0).tracks.audio.some((a) => a.fade_in || a.fade_out), "убрать фейды: все сняты");

// copy / paste: new ids, link kept inside the clip, pasted over what is there
const c = copy(base(), [{ track: "video", id: "v2" }])!;
const pr = paste(base(), c, 20);
const nv = pr.doc.tracks.video.find((b) => b.start === 20)!, na = pr.doc.tracks.audio.find((b) => b.start === 20)!;
ok(!!nv && nv.id !== "v2" && nv.src === "B" && na.link === nv.id, "вставка: копия блока со своим звуком, новые id, связь сохранена");
ok(pr.doc.tracks.video.find((b) => b.id === "v1")!.len === 20, "вставка: легла поверх (первый блок обрезан до 20 кадров)");

console.log(fails ? `\n${fails} проверок не прошло` : "\nвсе проверки пройдены");
if (fails) process.exit(1);

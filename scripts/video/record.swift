// Records Duracash at iPhone size for the promo, with WebKit (Safari's engine) and AVFoundation.
//
//   swiftc -O scripts/video/record.swift -o <tmp>/duracash-record
//   <tmp>/duracash-record scripts/video/shots.json <out folder>
//
// It loads the app from a local server in a 440x956 web view (iPhone Pro Max points), runs the
// steps in shots.json (reset the demo, sign in, go, tap, type, scroll, wait) and writes each
// "clip" to <out>/raw/<name>.mp4 at 3x (1320x2868), with a soft tap ring where a tap lands.
// No Safari toolbar or status bar is recorded, and nothing is sent: the demo account never emails.

import AppKit
import AVFoundation
import Foundation
import WebKit

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(1)
}

// 440x956 points at 3x = 1320x2868, the iPhone Pro Max screen the YuuP clips use. A width that
// isn't a multiple of 4 (430 x 3 = 1290) renders as a blank grey box in the editor's overlay pass.
let W: CGFloat = 440, H: CGFloat = 956, SCALE: CGFloat = 3

@MainActor
final class Recorder: NSObject, WKNavigationDelegate {
    let web: WKWebView
    let window: NSWindow
    let base: URL
    let out: URL
    var loaded: CheckedContinuation<Void, Never>?

    // Clip writing
    var writer: AVAssetWriter?
    var input: AVAssetWriterInput?
    var adaptor: AVAssetWriterInputPixelBufferAdaptor?
    var clipStart: CFTimeInterval = 0
    var lastPTS: Double = -1
    var busy = false
    var timer: Timer?

    init(base: URL, out: URL) {
        self.base = base
        self.out = out
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()          // a fresh sign-in every run
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: W, height: H), configuration: config)
        web.customUserAgent = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
            + "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
        // A floating, fully transparent window: WebKit keeps rendering and animating (an
        // off-screen or covered window is throttled), and nothing shows on the screen.
        window = NSWindow(contentRect: NSRect(x: 40, y: 40, width: W, height: H), styleMask: .borderless,
                          backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.level = .floating
        window.alphaValue = 0
        window.ignoresMouseEvents = true
        window.contentView = web
        super.init()
        web.navigationDelegate = self
        window.orderFrontRegardless()
    }

    nonisolated func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        Task { @MainActor in self.loaded?.resume(); self.loaded = nil }
    }

    func sleep(_ s: Double) async { try? await Task.sleep(nanoseconds: UInt64(s * 1e9)) }

    @discardableResult
    func js(_ code: String) async -> Any? {
        do { return try await web.callAsyncJavaScript(code, arguments: [:], contentWorld: .page) }
        catch { FileHandle.standardError.write("js error: \(error)\n".data(using: .utf8)!); return nil }
    }

    func go(_ path: String) async {
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
            loaded = c
            web.load(URLRequest(url: URL(string: path, relativeTo: base)!))
        }
        await sleep(0.9)                                    // the app's own fetch and render
        await tidy()
    }

    /// Promo hygiene, re-applied after every load: no demo tip, and no "not sent: demo account"
    /// toast (the demo never emails; a real account does).
    func tidy() async {
        await js("""
        const hide = () => {
          document.querySelectorAll('.notice, .toast, [role=status], [role=alert]').forEach(el => {
            if (/demo/i.test(el.textContent || '')) el.style.display = 'none';
          });
          // The demo never emails, so its sends read "email failed"; a real account's don't.
          document.querySelectorAll('.row-sub, td').forEach(el => {
            for (const n of el.childNodes) if (n.nodeType === 3 && n.data.includes(' · email failed')) n.data = n.data.replace(' · email failed', '');
          });
        };
        hide();
        if (!window.__tidy) { window.__tidy = new MutationObserver(hide); window.__tidy.observe(document.body, {childList: true, subtree: true, characterData: true}); }
        """)
    }

    // MARK: steps

    func tap(_ text: String, nth: Int = 0) async {
        let found = await js("""
        const want = \(jsString(text)).toLowerCase();
        const starts = e => e.offsetParent !== null && (e.innerText || e.getAttribute('aria-label') || '').trim().toLowerCase().startsWith(want);
        // An open confirm dialog first: its button shares a name with the one that opened it.
        const scope = document.querySelector('dialog[open]') || document;
        let els = [...scope.querySelectorAll('button, a, [role=button], summary, label')].filter(starts);
        // An exact name wins over a longer one that starts the same ("Record" vs "Record a payment").
        const exact = els.filter(e => (e.innerText || e.getAttribute('aria-label') || '').trim().toLowerCase() === want);
        if (exact.length) els = exact;
        // A clickable card (no button inside): the innermost element whose text starts with it.
        if (!els.length) els = [...document.querySelectorAll('main *')].filter(e => starts(e) && ![...e.children].some(starts));
        const el = els[\(nth)];
        if (!el) return false;
        const r0 = el.getBoundingClientRect();
        if (r0.top < 90 || r0.bottom > innerHeight - 40) {
          const target = scrollY + r0.top - innerHeight * 0.45;
          const from = scrollY, t0 = performance.now(), dur = 650;
          await new Promise(res => { const step = now => { const k = Math.min(1, (now - t0) / dur);
            scrollTo(0, from + (target - from) * (1 - Math.pow(1 - k, 3))); k < 1 ? requestAnimationFrame(step) : res(); };
            requestAnimationFrame(step); });
          await new Promise(res => setTimeout(res, 250));
        }
        const r = el.getBoundingClientRect();
        const ring = document.createElement('div');
        Object.assign(ring.style, {position: 'fixed', left: (r.left + r.width / 2 - 26) + 'px', top: (r.top + r.height / 2 - 26) + 'px',
          width: '52px', height: '52px', borderRadius: '50%', background: 'rgba(140,63,29,0.22)',
          border: '2px solid rgba(140,63,29,0.6)', pointerEvents: 'none', zIndex: 2147483647});
        document.body.appendChild(ring);
        ring.animate([{transform: 'scale(0.45)', opacity: 1}, {transform: 'scale(1.35)', opacity: 0}], {duration: 650, easing: 'ease-out'});
        setTimeout(() => ring.remove(), 700);
        await new Promise(res => setTimeout(res, 220));
        el.click();
        return true;
        """) as? Bool ?? false
        if !found { fail("tap: nothing visible starts with \"\(text)\"") }
    }

    func type(into hint: String, text: String) async {
        let ok = await js("""
        const hint = \(jsString(hint)).toLowerCase();
        const el = [...document.querySelectorAll('input, textarea')].find(e => e.offsetParent !== null &&
          ((e.placeholder || '') + ' ' + (e.getAttribute('aria-label') || '') + ' ' + (e.name || '')).toLowerCase().includes(hint));
        if (!el) return false;
        el.focus();
        const text = \(jsString(text));
        for (let i = 1; i <= text.length; i++) {
          el.value = text.slice(0, i);
          el.dispatchEvent(new Event('input', {bubbles: true}));
          await new Promise(res => setTimeout(res, 45));
        }
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
        """) as? Bool ?? false
        if !ok { fail("type: no visible field matching \"\(hint)\"") }
    }

    func scroll(by dy: Double, seconds: Double) async {
        await js("""
        const from = scrollY, to = from + \(dy), t0 = performance.now(), dur = \(seconds * 1000);
        await new Promise(res => { const step = now => { const k = Math.min(1, (now - t0) / dur);
          scrollTo(0, from + (to - from) * (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2));
          k < 1 ? requestAnimationFrame(step) : res(); }; requestAnimationFrame(step); });
        """)
    }

    // MARK: capture

    func startClip(_ name: String) throws {
        let dir = out.appendingPathComponent("raw")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("\(name).mp4")
        try? FileManager.default.removeItem(at: url)
        let w = try AVAssetWriter(outputURL: url, fileType: .mp4)
        let pw = Int(W * SCALE), ph = Int(H * SCALE)
        let inp = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: pw, AVVideoHeightKey: ph,
            // Tagged Rec. 709 like the Simulator's recordings; untagged clips come out as a blank
            // grey box once the editor's overlay (AVVideoCompositionCoreAnimationTool) runs.
            AVVideoColorPropertiesKey: [AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
                                        AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2,
                                        AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2],
            AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 14_000_000]])
        inp.expectsMediaDataInRealTime = true
        let ad = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: inp, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: pw, kCVPixelBufferHeightKey as String: ph])
        w.add(inp)
        guard w.startWriting() else { fail("can't write \(url.path): \(String(describing: w.error))") }
        w.startSession(atSourceTime: .zero)
        writer = w; input = inp; adaptor = ad
        clipStart = CACurrentMediaTime(); lastPTS = -1
        grab()
        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30, repeats: true) { _ in Task { @MainActor in self.grab() } }
        print("  recording \(name)")
    }

    func grab() {
        guard !busy, let ad = adaptor, let inp = input, inp.isReadyForMoreMediaData else { return }
        busy = true
        let cfg = WKSnapshotConfiguration()
        cfg.snapshotWidth = NSNumber(value: Double(W * SCALE))
        cfg.afterScreenUpdates = false
        // The first frame is stamped at zero: a clip that starts with no picture breaks the
        // editor (a title card borrows a clip's first tenth of a second).
        let t = lastPTS < 0 ? 0 : CACurrentMediaTime() - clipStart
        web.takeSnapshot(with: cfg) { image, _ in
            Task { @MainActor in
                defer { self.busy = false }
                guard let image, self.writer != nil, t > self.lastPTS + 0.001,
                      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil),
                      let pool = ad.pixelBufferPool else { return }
                var pb: CVPixelBuffer?
                CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pb)
                guard let px = pb else { return }
                CVPixelBufferLockBaseAddress(px, [])
                let ctx = CGContext(data: CVPixelBufferGetBaseAddress(px), width: CVPixelBufferGetWidth(px),
                                    height: CVPixelBufferGetHeight(px), bitsPerComponent: 8,
                                    bytesPerRow: CVPixelBufferGetBytesPerRow(px), space: CGColorSpaceCreateDeviceRGB(),
                                    bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)!
                ctx.draw(cg, in: CGRect(x: 0, y: 0, width: CVPixelBufferGetWidth(px), height: CVPixelBufferGetHeight(px)))
                CVPixelBufferUnlockBaseAddress(px, [])
                if ad.append(px, withPresentationTime: CMTime(seconds: t, preferredTimescale: 600)) { self.lastPTS = t }
            }
        }
    }

    func endClip() async {
        timer?.invalidate(); timer = nil
        while busy { await sleep(0.02) }
        grab(); while busy { await sleep(0.02) }               // a last frame at the end time
        guard let w = writer, let inp = input else { return }
        inp.markAsFinished()
        await w.finishWriting()
        if w.status != .completed { fail("clip failed: \(String(describing: w.error))") }
        print(String(format: "  wrote %@ (%.1f s)", w.outputURL.lastPathComponent, CACurrentMediaTime() - clipStart))
        writer = nil; input = nil; adaptor = nil
    }

    // MARK: run

    func run(_ steps: [[String: Any]]) async {
        for step in steps {
            if let s = step["wait"] as? Double { await sleep(s) }
            if step["reset"] as? Bool == true {
                await go("/")
                await js("await fetch('/api/reset', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});")
            }
            if let login = step["login"] as? [String] {
                await go("/")
                await js("""
                await fetch('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({email: \(jsString(login[0])), password: \(jsString(login[1]))})});
                """)
            }
            if let path = step["go"] as? String { await go(path) }
            if let code = step["js"] as? String { await js(code); await tidy() }
            if let name = step["clip"] as? String { do { try startClip(name) } catch { fail("\(error)") } }
            if let text = step["tap"] as? String { await tap(text, nth: step["nth"] as? Int ?? 0); await sleep(step["then"] as? Double ?? 0.9); await tidy() }
            if let t = step["type"] as? [String] { await type(into: t[0], text: t[1]) }
            if let dy = step["scroll"] as? Double { await scroll(by: dy, seconds: step["seconds"] as? Double ?? 1.0) }
            if step["end"] as? Bool == true { await endClip() }
        }
    }
}

func jsString(_ s: String) -> String {
    let data = try! JSONSerialization.data(withJSONObject: [s])
    let arr = String(data: data, encoding: .utf8)!
    return String(arr.dropFirst().dropLast())
}

let args = CommandLine.arguments
guard args.count >= 3 else { fail("usage: duracash-record shots.json out_folder") }
guard let data = try? Data(contentsOf: URL(fileURLWithPath: args[1])),
      let spec = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      let steps = spec["steps"] as? [[String: Any]] else { fail("bad shots file \(args[1])") }
let base = URL(string: spec["base"] as? String ?? "http://127.0.0.1:8765")!
let out = URL(fileURLWithPath: args[2])

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
Task { @MainActor in
    let rec = Recorder(base: base, out: out)
    await rec.run(steps)
    print("done")
    exit(0)
}
app.run()

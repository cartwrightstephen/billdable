// Video editing for the Duracash promo, with AVFoundation only (no ffmpeg).
// Adapted from the YuuP editor: Duracash's colors and Georgia wordmark.
//
//   swift scripts/video/edit.swift analyze raw/02_today.mp4 [hold]
//       Prints the time ranges where the screen changes (plus a short hold
//       after each change). The pauses between recorded taps are left out.
//   swift scripts/video/edit.swift compose spec.json
//       Builds a 1080x1920 video from title cards and app clips (record.swift): device
//       frame with rounded corners, a section label, the narration as subtitles
//       and the narration audio. See scripts/video/README.md for the spec.
//   swift scripts/video/edit.swift frames video.mp4 out_prefix t1 t2 ...
//       Saves stills (PNG) at the given seconds, for checking a render.

import AVFoundation
import AppKit
import Foundation
import QuartzCore

// MARK: - Helpers

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(1)
}

func cm(_ s: Double) -> CMTime { CMTime(seconds: s, preferredTimescale: 600) }

func color(_ rgb: [Double], _ a: Double = 1) -> CGColor {
    CGColor(red: rgb[0] / 255, green: rgb[1] / 255, blue: rgb[2] / 255, alpha: a)
}

func duration(of url: URL) -> Double {
    let asset = AVURLAsset(url: url)
    return CMTimeGetSeconds(asset.duration)
}

// MARK: - analyze

struct Span { var start: Double; var end: Double; var tail: Double }

/// Change detection on a sparse luma grid: a frame "moves" when more than
/// `cells` grid points differ by more than 14 levels from the previous frame.
/// A blinking text cursor touches one or two points, so it doesn't count.
func analyze(_ url: URL, hold: Double, lead: Double = 0.9) -> (spans: [Span], duration: Double) {
    let asset = AVURLAsset(url: url)
    guard let track = asset.tracks(withMediaType: .video).first,
          let reader = try? AVAssetReader(asset: asset) else { fail("can't read \(url.path)") }
    let out = AVAssetReaderTrackOutput(track: track, outputSettings: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
    out.alwaysCopiesSampleData = false
    reader.add(out)
    reader.startReading()
    let total = CMTimeGetSeconds(asset.duration)
    var prev: [UInt8] = []
    var moving: [Double] = []
    let cols = 48, rows = 100
    while let sample = out.copyNextSampleBuffer() {
        guard let px = CMSampleBufferGetImageBuffer(sample) else { continue }
        let t = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample))
        CVPixelBufferLockBaseAddress(px, .readOnly)
        let w = CVPixelBufferGetWidth(px), h = CVPixelBufferGetHeight(px)
        let bpr = CVPixelBufferGetBytesPerRow(px)
        let base = CVPixelBufferGetBaseAddress(px)!.assumingMemoryBound(to: UInt8.self)
        var grid = [UInt8](repeating: 0, count: cols * rows)
        for r in 0..<rows {
            let y = min(h - 1, (r * h + h / 2) / rows)
            for c in 0..<cols {
                let x = min(w - 1, (c * w + w / 2) / cols)
                let p = base + y * bpr + x * 4
                grid[r * cols + c] = UInt8((Int(p[0]) + 2 * Int(p[1]) + Int(p[2])) / 4)
            }
        }
        CVPixelBufferUnlockBaseAddress(px, .readOnly)
        if !prev.isEmpty {
            var n = 0
            for i in 0..<grid.count where abs(Int(grid[i]) - Int(prev[i])) > 14 { n += 1 }
            if n > 3 { moving.append(t) }
        }
        prev = grid
    }
    // Movement into spans (gaps under 0.4 s join), then pre-roll and hold.
    var spans: [Span] = []
    for t in moving {
        if let last = spans.last, t - last.end < 0.4 {
            spans[spans.count - 1].end = t
        } else {
            spans.append(Span(start: t, end: t, tail: 0))
        }
    }
    var kept: [Span] = []
    for (i, s) in spans.enumerated() {
        let pre = i == 0 ? lead : 0.15
        let start = max(0, s.start - pre)
        let nextStart = i + 1 < spans.count ? spans[i + 1].start - 0.15 : total
        let end = min(total, s.end + 0.1 + hold, max(s.end + 0.1, nextStart))
        let tail = max(0, end - (s.end + 0.1))
        if let last = kept.last, start <= last.end + 0.05 {
            kept[kept.count - 1].end = end
            kept[kept.count - 1].tail = tail
        } else {
            kept.append(Span(start: start, end: end, tail: tail))
        }
    }
    if kept.isEmpty { kept = [Span(start: 0, end: min(total, 2), tail: min(total, 2))] }
    return (kept, total)
}

// MARK: - frames

func frames(_ url: URL, prefix: String, times: [Double]) {
    let gen = AVAssetImageGenerator(asset: AVURLAsset(url: url))
    gen.appliesPreferredTrackTransform = true
    gen.requestedTimeToleranceBefore = .zero
    gen.requestedTimeToleranceAfter = .zero
    for t in times {
        guard let img = try? gen.copyCGImage(at: cm(t), actualTime: nil) else { print("no frame at \(t)"); continue }
        let rep = NSBitmapImageRep(cgImage: img)
        let path = "\(prefix)_\(String(format: "%06.2f", t)).png"
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
        print(path)
    }
}

// MARK: - compose

struct Line { let text: String; let audio: URL?; let length: Double }

struct Device {
    let kind: String          // "phone" | "watch"
    let frame: CGRect         // in top-left canvas coordinates
    let radius: CGFloat
}

let W: CGFloat = 1080, H: CGFloat = 1920
let BG: [Double] = [28, 25, 21]                 // Duracash ink, warm near-black
let ACCENT: [Double] = [224, 145, 95]           // copper, lightened for a dark background
let CREAM: [Double] = [246, 243, 238]           // Duracash paper
let MUTED: [Double] = [183, 173, 161], EDGE: [Double] = [74, 64, 55]

func device(_ kind: String, source: CGSize) -> Device {
    if kind == "watch" {
        let w: CGFloat = 860, h = w * source.height / source.width
        return Device(kind: kind, frame: CGRect(x: (W - w) / 2, y: 520, width: w, height: h), radius: w * 0.22)
    }
    let h: CGFloat = 1440, w = h * source.width / source.height
    return Device(kind: kind, frame: CGRect(x: (W - w) / 2, y: 430, width: w, height: h), radius: w * 0.125)
}

/// Canvas rect (top-left origin) to Core Animation (bottom-left origin).
func ca(_ r: CGRect) -> CGRect { CGRect(x: r.minX, y: H - r.maxY, width: r.width, height: r.height) }

/// Text drawn into a bitmap, top-aligned in `rect`. (CATextLayer draws nothing
/// in an export through AVVideoCompositionCoreAnimationTool.)
func textLayer(_ string: NSAttributedString, _ rect: CGRect) -> CALayer {
    let w = Int(rect.width), h = Int(rect.height)
    let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    let previous = NSGraphicsContext.current
    NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
    let bounds = string.boundingRect(with: CGSize(width: rect.width, height: .greatestFiniteMagnitude),
                                     options: [.usesLineFragmentOrigin, .usesFontLeading])
    let textHeight = min(rect.height, ceil(bounds.height))
    string.draw(with: CGRect(x: 0, y: rect.height - textHeight, width: rect.width, height: textHeight),
                options: [.usesLineFragmentOrigin, .usesFontLeading])
    NSGraphicsContext.current = previous
    let l = CALayer()
    l.contents = ctx.makeImage()
    l.frame = ca(rect)
    return l
}

func attr(_ s: String, size: CGFloat, weight: NSFont.Weight, rgb: [Double], kern: CGFloat = 0, serif: Bool = false) -> NSAttributedString {
    let p = NSMutableParagraphStyle()
    p.alignment = .center
    p.lineSpacing = size * 0.18
    return NSAttributedString(string: s, attributes: [
        .font: serif ? (NSFont(name: "Georgia", size: size) ?? NSFont.systemFont(ofSize: size, weight: weight))
                     : NSFont.systemFont(ofSize: size, weight: weight),
        .foregroundColor: NSColor(cgColor: color(rgb))!, .kern: kern, .paragraphStyle: p])
}

func show(_ layer: CALayer, from start: Double, length: Double, fade: Double = 0.25) {
    layer.opacity = 0
    let a = CAKeyframeAnimation(keyPath: "opacity")
    let f = max(0.001, min(0.45, fade / max(length, 0.01)))
    a.values = [0, 1, 1, 0]
    a.keyTimes = [0, NSNumber(value: f), NSNumber(value: 1 - f), 1]
    a.beginTime = AVCoreAnimationBeginTimeAtZero + start
    a.duration = length
    a.isRemovedOnCompletion = false
    a.fillMode = .both
    layer.add(a, forKey: nil)
}

func compose(specPath: String) {
    let specURL = URL(fileURLWithPath: specPath)
    let dir = specURL.deletingLastPathComponent()
    guard let data = try? Data(contentsOf: specURL),
          let spec = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let items = spec["items"] as? [[String: Any]] else { fail("bad spec \(specPath)") }
    func url(_ p: String) -> URL { p.hasPrefix("/") ? URL(fileURLWithPath: p) : dir.appendingPathComponent(p) }
    let gap = spec["voice_gap"] as? Double ?? 0.35
    let defaultHold = spec["hold"] as? Double ?? 1.4

    let comp = AVMutableComposition()
    guard let vTrack = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid),
          let aTrack = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)
    else { fail("can't make tracks") }
    let layerInstruction = AVMutableVideoCompositionLayerInstruction(assetTrack: vTrack)

    let parent = CALayer()
    parent.frame = CGRect(x: 0, y: 0, width: W, height: H)
    parent.backgroundColor = color(BG)
    let videoLayer = CALayer()
    videoLayer.frame = parent.frame
    parent.addSublayer(videoLayer)
    var overlays: [CALayer] = []

    // Title cards hide a short real segment (opacity 0) instead of an empty
    // edit: an empty edit at the start makes the export fail ("Operation Stopped").
    // A composition only points at its sources: every asset must stay alive
    // until the export ends, or it fails with "Operation Stopped".
    var keepAlive: [AVAsset] = []
    func open(_ u: URL) -> AVURLAsset { let a = AVURLAsset(url: u); keepAlive.append(a); return a }
    guard let firstSrc = items.compactMap({ $0["src"] as? String }).first,
          let blank = open(url(firstSrc)).tracks(withMediaType: .video).first
    else { fail("the spec needs at least one clip") }

    var t = 0.0
    var log: [[String: Any]] = []
    for item in items {
        let lines: [Line] = (item["lines"] as? [[String: Any]] ?? []).map { l in
            let a = (l["audio"] as? String).map(url)
            return Line(text: l["text"] as? String ?? "", audio: a, length: a.map(duration(of:)) ?? 0)
        }
        let narration = lines.isEmpty ? 0 : 0.45 + lines.map(\.length).reduce(0, +) + gap * Double(lines.count - 1) + 0.5
        let minLen = item["min"] as? Double ?? 0
        var length = max(narration, minLen)

        if let src = item["src"] as? String {
            let asset = open(url(src))
            guard let srcTrack = asset.tracks(withMediaType: .video).first else { fail("no video in \(src)") }
            let size = srcTrack.naturalSize.applying(srcTrack.preferredTransform)
            let source = CGSize(width: abs(size.width), height: abs(size.height))
            var spans: [Span]
            if let keep = item["keep"] as? [[Double]] {
                spans = keep.map { Span(start: $0[0], end: $0[1], tail: $0.count > 2 ? $0[2] : 0) }
            } else {
                spans = analyze(url(src), hold: item["hold"] as? Double ?? defaultHold).spans
            }
            if let from = item["from"] as? Double { spans = spans.compactMap { s in
                s.end <= from ? nil : Span(start: max(s.start, from), end: s.end, tail: min(s.tail, s.end - max(s.start, from))) } }
            if let to = item["to"] as? Double { spans = spans.compactMap { s in
                s.start >= to ? nil : Span(start: s.start, end: min(s.end, to), tail: max(0, s.tail - max(0, s.end - to))) } }
            var kept = spans.map { $0.end - $0.start }.reduce(0, +)
            if let maxLen = item["max"] as? Double, kept > maxLen {
                var left = maxLen
                spans = spans.compactMap { s in
                    guard left > 0.05 else { return nil }
                    let len = min(s.end - s.start, left); left -= len
                    return Span(start: s.start, end: s.start + len, tail: min(s.tail, len))
                }
                kept = maxLen
            }
            length = max(length, kept)
            // Stretch the still tails (a hold freezes the last frame) to cover the narration.
            let extra = length - kept
            let tails = spans.map(\.tail).reduce(0, +)
            let dev = device(item["device"] as? String ?? "phone", source: source)
            let s = dev.frame.width / source.width
            layerInstruction.setTransform(CGAffineTransform(scaleX: s, y: s)
                .concatenating(CGAffineTransform(translationX: dev.frame.minX, y: dev.frame.minY)), at: cm(t))
            layerInstruction.setOpacity(1, at: cm(t))
            var at = t
            for (i, sp) in spans.enumerated() {
                let moving = (sp.end - sp.start) - sp.tail
                if moving > 0.01 {
                    try! vTrack.insertTimeRange(CMTimeRange(start: cm(sp.start), duration: cm(moving)), of: srcTrack, at: cm(at))
                    at += moving
                }
                if sp.tail > 0.01 {
                    let share = tails > 0 ? extra * sp.tail / tails : (i == spans.count - 1 ? extra : 0)
                    let range = CMTimeRange(start: cm(sp.start + moving), duration: cm(sp.tail))
                    try! vTrack.insertTimeRange(range, of: srcTrack, at: cm(at))
                    if share > 0.01 {
                        vTrack.scaleTimeRange(CMTimeRange(start: cm(at), duration: cm(sp.tail)), toDuration: cm(sp.tail + share))
                    }
                    at += sp.tail + share
                } else if tails == 0 && i == spans.count - 1 && extra > 0.01 {
                    // No still tail: freeze by slowing the last 0.2 s.
                    let lastStart = at - 0.2
                    vTrack.scaleTimeRange(CMTimeRange(start: cm(lastStart), duration: cm(0.2)), toDuration: cm(0.2 + extra))
                    at += extra
                }
            }
            length = at - t
            // Device mask (rounded corners) and bezel for this item.
            let mask = CAShapeLayer()
            let path = CGMutablePath()
            path.addRect(CGRect(x: 0, y: 0, width: W, height: H))
            path.addPath(CGPath(roundedRect: ca(dev.frame), cornerWidth: dev.radius, cornerHeight: dev.radius, transform: nil))
            mask.path = path
            mask.fillRule = .evenOdd
            mask.fillColor = color(BG)
            mask.frame = parent.frame
            let bezel = CAShapeLayer()
            bezel.path = CGPath(roundedRect: ca(dev.frame.insetBy(dx: -7, dy: -7)), cornerWidth: dev.radius + 7,
                                cornerHeight: dev.radius + 7, transform: nil)
            bezel.fillColor = nil
            bezel.strokeColor = color(EDGE)
            bezel.lineWidth = 10
            bezel.frame = parent.frame
            for l in [mask, bezel] as [CALayer] { show(l, from: t, length: length, fade: 0.01); overlays.append(l) }
            log.append(["src": src, "start": t, "length": length, "kept": kept, "spans": spans.map { [$0.start, $0.end, $0.tail] }])
        } else {
            try! vTrack.insertTimeRange(CMTimeRange(start: .zero, duration: cm(0.1)), of: blank, at: cm(t))
            vTrack.scaleTimeRange(CMTimeRange(start: cm(t), duration: cm(0.1)), toDuration: cm(length))
            layerInstruction.setOpacity(0, at: cm(t))
            log.append(["title": item["title"] as? String ?? "", "start": t, "length": length])
        }

        // Title card text, or the section label above a clip.
        if item["src"] == nil {
            let title = NSMutableAttributedString()
            if (item["title"] as? String) == "Duracash" {
                title.append(attr("Duracash", size: 150, weight: .regular, rgb: CREAM, serif: true))
            } else {
                title.append(attr(item["title"] as? String ?? "", size: 84, weight: .heavy, rgb: CREAM))
            }
            let tl = textLayer(title, CGRect(x: 60, y: 640, width: W - 120, height: 220))
            show(tl, from: t, length: length, fade: 0.4)
            overlays.append(tl)
            if let sub = item["subtitle"] as? String {
                let sl = textLayer(attr(sub, size: 46, weight: .medium, rgb: MUTED), CGRect(x: 90, y: 880, width: W - 180, height: 400))
                show(sl, from: t, length: length, fade: 0.4)
                overlays.append(sl)
            }
        } else if let label = item["title"] as? String {
            let ll = textLayer(attr(label.uppercased(), size: 34, weight: .bold, rgb: ACCENT, kern: 4),
                               CGRect(x: 60, y: 70, width: W - 120, height: 50))
            show(ll, from: t, length: length)
            overlays.append(ll)
        }

        // Narration: audio and subtitles, one line after the other.
        var lt = t + 0.45
        for (i, line) in lines.enumerated() {
            if let a = line.audio, let src = open(a).tracks(withMediaType: .audio).first {
                try! aTrack.insertTimeRange(CMTimeRange(start: .zero, duration: cm(line.length)), of: src, at: cm(lt))
            }
            let end = i == lines.count - 1 ? t + length : lt + line.length + gap
            let y: CGFloat = item["src"] == nil ? 1340 : 135
            // A title card already shows its subtitle; don't print the same words twice.
            if item["src"] == nil, line.text == item["subtitle"] as? String { lt += line.length + gap; continue }
            let sub = textLayer(attr(line.text, size: item["src"] == nil ? 44 : 40, weight: .medium, rgb: CREAM),
                                CGRect(x: 70, y: y, width: W - 140, height: 270))
            show(sub, from: lt - 0.15, length: end - lt + 0.15, fade: 0.15)
            overlays.append(sub)
            lt += line.length + gap
        }
        t += length
    }
    for l in overlays { parent.addSublayer(l) }

    let instruction = AVMutableVideoCompositionInstruction()
    instruction.timeRange = CMTimeRange(start: .zero, duration: comp.duration)
    instruction.backgroundColor = color(BG)
    instruction.layerInstructions = [layerInstruction]
    let vc = AVMutableVideoComposition()
    vc.renderSize = CGSize(width: W, height: H)
    vc.frameDuration = CMTime(value: 1, timescale: 30)
    vc.instructions = [instruction]
    if ProcessInfo.processInfo.environment["NOANIM"] == nil {
        vc.animationTool = AVVideoCompositionCoreAnimationTool(postProcessingAsVideoLayer: videoLayer, in: parent)
    }

    let outURL = url(spec["out"] as? String ?? "out.mp4")
    try? FileManager.default.removeItem(at: outURL)
    guard let export = AVAssetExportSession(asset: comp, presetName: spec["preset"] as? String ?? AVAssetExportPresetHighestQuality)
    else { fail("no export session") }
    if ProcessInfo.processInfo.environment["NOVC"] == nil { export.videoComposition = vc }
    export.outputURL = outURL
    export.outputFileType = .mp4
    export.shouldOptimizeForNetworkUse = true
    let done = DispatchSemaphore(value: 0)
    export.exportAsynchronously { done.signal() }
    while done.wait(timeout: .now() + 5) == .timedOut {
        print(String(format: "  exporting %.0f%%", export.progress * 100))
    }
    _ = keepAlive.count
    if export.status != .completed { fail("export failed: \(String(describing: export.error))") }
    let logURL = outURL.deletingPathExtension().appendingPathExtension("timeline.json")
    if let j = try? JSONSerialization.data(withJSONObject: log, options: [.prettyPrinted]) { try? j.write(to: logURL) }
    print(String(format: "wrote %@ (%.1f s)", outURL.path, t))
}

// MARK: - main

let args = CommandLine.arguments
guard args.count >= 3 else { fail("usage: edit.swift analyze|compose|frames …") }
switch args[1] {
case "analyze":
    let r = analyze(URL(fileURLWithPath: args[2]), hold: args.count > 3 ? Double(args[3]) ?? 1.4 : 1.4)
    let kept = r.spans.map { $0.end - $0.start }.reduce(0, +)
    let j: [String: Any] = ["duration": r.duration, "kept": kept, "spans": r.spans.map { [$0.start, $0.end, $0.tail] }]
    print(String(data: try! JSONSerialization.data(withJSONObject: j), encoding: .utf8)!)
case "compose":
    compose(specPath: args[2])
case "frames":
    frames(URL(fileURLWithPath: args[2]), prefix: args[3], times: args.dropFirst(4).compactMap(Double.init))
default:
    fail("unknown command \(args[1])")
}

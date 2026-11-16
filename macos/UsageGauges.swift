import SwiftUI
import AppKit
import Combine

// Claude plan usage as two tiny gauges (weekly and five hour), for any SwiftUI Mac app, plus an optional
// menu bar item. It reads the JSON that claude_usage_watch.py writes (JSON_OUTPUT), served over HTTP by any
// web server you choose. Set `usageJSONURL` below.
// This file never talks to Anthropic and never touches the Claude login. When the URL does not answer,
// the last numbers stay, dimmed.

/// Where the gauge JSON is served. Change this.
let usageJSONURL = URL(string: "http://localhost:8123/claude-usage.json")!

struct UsageNumbers: Decodable {
    let week_pct: Double
    let week_expected: Double
    let five_pct: Double
    let week_resets_ts: Double?
    let five_resets_ts: Double?
}

final class ClaudeUsage: ObservableObject {
    static let shared = ClaudeUsage()
    @Published var numbers: UsageNumbers?
    @Published var fetchedAt: Date?
    @Published var lastError: String?
    private var timer: Timer?
    private var lastTry = Date.distantPast
    private let url = usageJSONURL

    private init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.refresh() }
    }

    /// Safe to call often (for example every time a view appears): it fetches at most every 20 seconds.
    func refresh() {
        guard Date().timeIntervalSince(lastTry) > 20 else { return }
        lastTry = Date()
        var req = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 3)
        req.httpMethod = "GET"
        URLSession.shared.dataTask(with: req) { data, _, err in
            guard let data = data, let n = try? JSONDecoder().decode(UsageNumbers.self, from: data) else {
                // Failed (server down, or Local Network permission not given yet): say why and try again soon.
                DispatchQueue.main.async {
                    self.lastError = err?.localizedDescription ?? "the server answered, but not with usage numbers"
                    self.lastTry = Date.distantPast
                    DispatchQueue.main.asyncAfter(deadline: .now() + 30) { self.refresh() }
                }
                return
            }
            DispatchQueue.main.async {
                self.numbers = n
                self.fetchedAt = Date()
                self.lastError = nil
            }
        }.resume()
    }

    var stale: Bool {
        guard let at = fetchedAt else { return true }
        return Date().timeIntervalSince(at) > 15 * 60
    }
}

/// Half circle from the left (0) over the top to the right (1).
struct UsageArc: Shape {
    var from: Double
    var to: Double
    func path(in rect: CGRect) -> Path {
        var p = Path()
        let c = CGPoint(x: rect.midX, y: rect.maxY)
        let r = min(rect.width / 2, rect.height)
        p.addArc(center: c, radius: r, startAngle: .degrees(180 + 180 * from), endAngle: .degrees(180 + 180 * to), clockwise: false)
        return p
    }
}

struct UsageNeedle: Shape {
    var at: Double
    func path(in rect: CGRect) -> Path {
        var p = Path()
        let c = CGPoint(x: rect.midX, y: rect.maxY)
        let r = min(rect.width / 2, rect.height) * 0.78
        let a = (180 + 180 * at) * Double.pi / 180
        p.move(to: c)
        p.addLine(to: CGPoint(x: c.x + r * CGFloat(cos(a)), y: c.y + r * CGFloat(sin(a))))
        return p
    }
}

struct MiniGauge: View {
    let pct: Double        // 0 to 100
    let expected: Double?  // weekly only: where an even pace would be now
    let label: String

    private var color: Color {
        if pct >= 90 { return Color.red }
        if pct >= 75 { return Color.orange }
        if let e = expected, pct - e >= 10 { return Color.orange }
        return Color.teal
    }

    var body: some View {
        let f = max(0, min(1, pct / 100))
        HStack(spacing: 5) {
            ZStack {
                UsageArc(from: 0, to: 1)
                    .stroke(Color.primary.opacity(0.15), style: StrokeStyle(lineWidth: 4.5, lineCap: .round))
                UsageArc(from: 0, to: max(0.02, f))
                    .stroke(color, style: StrokeStyle(lineWidth: 4.5, lineCap: .round))
                UsageNeedle(at: f)
                    .stroke(Color.primary.opacity(0.85), style: StrokeStyle(lineWidth: 1.8, lineCap: .round))
            }
            .padding(.horizontal, 3)
            .padding(.top, 3)
            .frame(width: 44, height: 24)
            Text("\(label) \(Int(pct.rounded()))%")
                .font(.system(size: 13, weight: .medium))
                .monospacedDigit()
        }
    }
}

func usageLeft(_ ts: Double?) -> String {
    guard let ts = ts else { return "unknown" }
    let s = Int(ts - Date().timeIntervalSince1970)
    if s <= 0 { return "resetting" }
    if s >= 86400 { return "\(s / 86400) d \((s % 86400) / 3600) h" }
    if s >= 3600 { return "\(s / 3600) h \((s % 3600) / 60) min" }
    return "\(max(1, s / 60)) min"
}

/// Two tiny gauges: weekly (W) and five hour (5h). Shows nothing until the first numbers arrive.
struct UsageGauges: View {
    @ObservedObject var usage = ClaudeUsage.shared

    var body: some View {
        Group {
            if let n = usage.numbers {
                HStack(spacing: 12) {
                    MiniGauge(pct: n.week_pct, expected: n.week_expected, label: "W")
                        .help("Claude weekly: \(Int(n.week_pct))% used, an even pace would be \(Int(n.week_expected))%. Resets in \(usageLeft(n.week_resets_ts)).")
                    MiniGauge(pct: n.five_pct, expected: nil, label: "5h")
                        .help("Claude five hour: \(Int(n.five_pct))% used. Resets in \(usageLeft(n.five_resets_ts)).")
                }
                .opacity(usage.stale ? 0.4 : 1)
            }
        }
        .onAppear { usage.refresh() }
    }
}

/// Optional: the same two gauges in the Mac menu bar, always visible: gauge, number, gauge, number.
/// Call `UsageMenuBar.shared.start()` once at launch. Hover shows the time left to each reset.
final class UsageMenuBar {
    static let shared = UsageMenuBar()
    private var item: NSStatusItem?
    private var sub: AnyCancellable?
    private var timer: Timer?

    func start() {
        guard item == nil else { return }
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        sub = ClaudeUsage.shared.objectWillChange.receive(on: RunLoop.main).sink { [weak self] _ in
            DispatchQueue.main.async { self?.render() }
        }
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.render() }
        render()
    }

    private func render() {
        guard let button = item?.button else { return }
        guard let n = ClaudeUsage.shared.numbers else {
            button.image = nil
            button.title = "Claude -"
            button.toolTip = "No usage numbers yet. " + (ClaudeUsage.shared.lastError ?? "Waiting for the server.")
            return
        }
        button.title = ""
        button.image = UsageMenuBar.picture(week: n.week_pct, expected: n.week_expected, five: n.five_pct)
        button.alphaValue = ClaudeUsage.shared.stale ? 0.45 : 1
        button.toolTip = "Claude weekly \(Int(n.week_pct))% (even pace \(Int(n.week_expected))%), resets in \(usageLeft(n.week_resets_ts)). Five hour \(Int(n.five_pct))%, resets in \(usageLeft(n.five_resets_ts))."
    }

    private static func tint(_ pct: Double, _ expected: Double?) -> NSColor {
        if pct >= 90 { return NSColor.systemRed }
        if pct >= 75 { return NSColor.systemOrange }
        if let e = expected, pct - e >= 10 { return NSColor.systemOrange }
        return NSColor.systemTeal
    }

    private static func picture(week: Double, expected: Double, five: Double) -> NSImage {
        let font = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .medium)
        let texts = ["\(Int(week.rounded()))%", "\(Int(five.rounded()))%"]
        let pcts = [week, five]
        let tints = [tint(week, expected), tint(five, nil)]
        let gaugeW: CGFloat = 20, gap: CGFloat = 3, between: CGFloat = 8
        let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.labelColor]
        // tiny dim letters say which is which: W = weekly, 5h = five hour
        let tags = ["W", "5h"]
        let tagAttrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 9, weight: .semibold), .foregroundColor: NSColor.secondaryLabelColor]
        let tagGap: CGFloat = 2
        let tagWidths = tags.map { ($0 as NSString).size(withAttributes: tagAttrs).width }
        let widths = texts.map { ($0 as NSString).size(withAttributes: attrs).width }
        let total = gaugeW + gap + tagWidths[0] + tagGap + widths[0] + between + gaugeW + gap + tagWidths[1] + tagGap + widths[1]
        let size = NSSize(width: ceil(total), height: 18)
        return NSImage(size: size, flipped: false) { _ in
            var x: CGFloat = 0
            for i in 0..<2 {
                let f = CGFloat(max(0, min(1, pcts[i] / 100)))
                let c = NSPoint(x: x + gaugeW / 2, y: 5)
                let r: CGFloat = 8
                let track = NSBezierPath()
                track.appendArc(withCenter: c, radius: r, startAngle: 180, endAngle: 0, clockwise: true)
                track.lineWidth = 3
                track.lineCapStyle = .round
                NSColor.labelColor.withAlphaComponent(0.25).setStroke()
                track.stroke()
                let fill = NSBezierPath()
                fill.appendArc(withCenter: c, radius: r, startAngle: 180, endAngle: 180 - 180 * max(0.03, f), clockwise: true)
                fill.lineWidth = 3
                fill.lineCapStyle = .round
                tints[i].setStroke()
                fill.stroke()
                let a = (180 - 180 * f) * CGFloat.pi / 180
                let needle = NSBezierPath()
                needle.move(to: c)
                needle.line(to: NSPoint(x: c.x + (r - 2) * cos(a), y: c.y + (r - 2) * sin(a)))
                needle.lineWidth = 1.3
                needle.lineCapStyle = .round
                NSColor.labelColor.setStroke()
                needle.stroke()
                x += gaugeW + gap
                (tags[i] as NSString).draw(at: NSPoint(x: x, y: 3.5), withAttributes: tagAttrs)
                x += tagWidths[i] + tagGap
                (texts[i] as NSString).draw(at: NSPoint(x: x, y: 2), withAttributes: attrs)
                x += widths[i] + between
            }
            return true
        }
    }
}

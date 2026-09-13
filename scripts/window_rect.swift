// Reports where a process's windows are, whether that process is the active app, and what
// displays exist — everything the capture scripts need to crop a recording to the game's
// client area without guessing.
//
// usage:  window_rect <pid>        (pid 0 lists displays only)
// output: one line of JSON
//
// Needs no Accessibility permission: CGWindowListCopyWindowInfo bounds and NSRunningApplication
// state are readable by any process. Coordinates are global, top-left origin, in points — on a
// Retina display multiply by `scale` to get capture pixels.

import AppKit
import CoreGraphics
import Foundation

let pid = Int32(CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "0") ?? 0
var out: [String: Any] = ["pid": Int(pid)]

if pid > 0, let app = NSRunningApplication(processIdentifier: pid) {
    out["active"] = app.isActive
    out["name"] = app.localizedName ?? ""
} else {
    out["active"] = false
}

var windows: [[String: Any]] = []
let list = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as NSArray?) ?? []
for case let w as NSDictionary in list {
    guard pid > 0, (w[kCGWindowOwnerPID as String] as? Int) == Int(pid) else { continue }
    guard let bd = w[kCGWindowBounds as String] as? NSDictionary,
          let r = CGRect(dictionaryRepresentation: bd as CFDictionary) else { continue }
    windows.append([
        "x": Int(r.origin.x), "y": Int(r.origin.y),
        "w": Int(r.width), "h": Int(r.height),
        "layer": (w[kCGWindowLayer as String] as? Int) ?? -1,
    ])
}
out["windows"] = windows

var displays: [[String: Any]] = []
let mainID = CGMainDisplayID()
for screen in NSScreen.screens {
    let id = (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? 0
    let b = CGDisplayBounds(id)
    displays.append([
        "id": Int(id),
        "x": Int(b.origin.x), "y": Int(b.origin.y),
        "w": Int(b.width), "h": Int(b.height),
        "main": id == mainID,
        "scale": Double(screen.backingScaleFactor),
        "name": screen.localizedName,
    ])
}
out["displays"] = displays

let data = try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys])
print(String(data: data, encoding: .utf8)!)

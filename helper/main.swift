// BushDumpWiFiHelper — reads WiFi network names for bushdump.
//
// macOS 26+ only tells an app which network it is on, or what the networks in
// range are called, if that app has Location permission. A command-line process
// can't hold that permission (it is attributed to the terminal), so bushdump
// asks this bundle instead, launched through LaunchServices:
//
//     open -W -g -n BushDumpWiFiHelper.app --args <command> <outfile>
//
// <command> is `authorize` (ask for Location once), `current` or `scan`. The
// reply is one JSON object written to <outfile>; an app launched by `open` has
// no stdout worth reading.

import CoreLocation
import CoreWLAN
import Foundation

final class AuthorizationWatcher: NSObject, CLLocationManagerDelegate {
    var onChange: (() -> Void)?
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) { onChange?() }
}

let arguments = CommandLine.arguments
guard arguments.count == 3, ["authorize", "current", "scan"].contains(arguments[1]) else {
    FileHandle.standardError.write(Data("usage: BushDumpWiFiHelper authorize|current|scan <outfile>\n".utf8))
    exit(64)
}
let command = arguments[1]
let outfile = URL(fileURLWithPath: arguments[2])

let manager = CLLocationManager()
let watcher = AuthorizationWatcher()
manager.delegate = watcher

func isAuthorized() -> Bool {
    let status = manager.authorizationStatus
    return status == .authorizedAlways || status == .authorized
}

func reply() -> Never {
    let interface = CWWiFiClient.shared().interface()
    var result: [String: Any] = [
        "authorized": isAuthorized(),
        "ssid": interface?.ssid() ?? NSNull(),
        "bssid": interface?.bssid() ?? NSNull(),
        "networks": [String](),
    ]
    if command == "scan", let interface, let found = try? interface.scanForNetworks(withName: nil) {
        result["networks"] = Array(Set(found.compactMap { $0.ssid })).sorted()
    }
    let data = (try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])) ?? Data()
    try? data.write(to: outfile)
    exit(0)
}

// The status a fresh CLLocationManager reports is not settled until its first
// delegate callback, so every command waits for that before answering.
var settled = false
watcher.onChange = {
    if command == "authorize" && manager.authorizationStatus == .notDetermined {
        if !settled { manager.requestAlwaysAuthorization() }
        settled = true
        return
    }
    reply()
}
let patience: TimeInterval = command == "authorize" ? 120 : 2
DispatchQueue.main.asyncAfter(deadline: .now() + patience) { reply() }
RunLoop.main.run()

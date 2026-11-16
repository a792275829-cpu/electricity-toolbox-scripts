// Inspect only the explicitly selected process. No permission prompts or writes
// unless --press / --set is explicitly supplied. Run with `swift this-file ...`.
import AppKit
import ApplicationServices

func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}

func string(_ element: AXUIElement, _ name: String) -> String {
    guard let value = attribute(element, name) else { return "" }
    return String(describing: value).prefix(1500).description
}

func emit(_ value: [String: Any], code: Int32 = 0) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys])
    print(String(data: data, encoding: .utf8)!)
    exit(code)
}

let args = Array(CommandLine.arguments.dropFirst())
guard args.count >= 2, args[0] == "--pid", let pid = Int32(args[1]), pid > 0 else {
    emit(["error": "Usage: --pid PID [--press NAME | --set NAME VALUE]"], code: 2)
}
let operation = Array(args.dropFirst(2))
guard operation.isEmpty || (operation.count == 2 && operation[0] == "--press")
        || (operation.count == 3 && operation[0] == "--set") else {
    emit(["error": "Invalid operation"], code: 2)
}
guard AXIsProcessTrusted() else {
    emit(["error": "Controlling process does not have accessibility permission", "trusted": false], code: 2)
}
guard NSRunningApplication(processIdentifier: pid) != nil else {
    emit(["error": "Target process is not running", "pid": pid], code: 2)
}
let application = AXUIElementCreateApplication(pid)
AXUIElementSetMessagingTimeout(application, 3)
var elements: [AXUIElement] = []
var nodes: [[String: Any]] = []
var traversalErrors: [String] = []

func walk(_ element: AXUIElement, _ depth: Int) {
    if depth > 20 || nodes.count >= 500 { return }
    if elements.contains(where: { CFEqual($0, element) }) { return }
    elements.append(element)
    var settable = DarwinBoolean(false)
    let settableError = AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &settable)
    var actions: CFArray?
    let actionsError = AXUIElementCopyActionNames(element, &actions)
    let identifier = string(element, kAXIdentifierAttribute)
    let secondIdentifier = string(element, kAXIdentifierAttribute)
    nodes.append([
        "depth": depth, "role": string(element, kAXRoleAttribute),
        "title": string(element, kAXTitleAttribute),
        "description": string(element, kAXDescriptionAttribute),
        "value": string(element, kAXValueAttribute),
        "enabled": string(element, kAXEnabledAttribute),
        "identifier": identifier, "secondIdentifier": secondIdentifier,
        "identifierStable": identifier == secondIdentifier,
        "valueSettable": settable.boolValue,
        "settableError": settableError.rawValue,
        "actions": actionsError == .success ? (actions as? [String] ?? []) : [],
    ])
    var children: CFTypeRef?
    let childAttribute = depth == 0 ? kAXWindowsAttribute : kAXChildrenAttribute
    let error = AXUIElementCopyAttributeValue(element, childAttribute as CFString, &children)
    if error == .success, let children = children as? [AXUIElement] {
        for child in children { walk(child, depth + 1) }
    } else if error != .success && error != .attributeUnsupported && error != .noValue {
        traversalErrors.append(String(error.rawValue))
    }
}

walk(application, 0)
var output: [String: Any] = ["pid": pid, "trusted": true, "nodes": nodes,
                            "traversalErrors": traversalErrors]
if !operation.isEmpty {
    let name = operation[1]
    let matches = elements.filter {
        string($0, kAXTitleAttribute) == name || string($0, kAXDescriptionAttribute) == name
    }
    guard matches.count == 1 else {
        output["error"] = "Expected one matching control, found \(matches.count): \(name)"
        emit(output, code: 2)
    }
    let target = matches[0]
    let result: AXError
    if operation[0] == "--press" {
        result = AXUIElementPerformAction(target, kAXPressAction as CFString)
    } else {
        result = AXUIElementSetAttributeValue(target, kAXValueAttribute as CFString, operation[2] as CFString)
    }
    Thread.sleep(forTimeInterval: 0.15)
    output["operation"] = operation[0]
    output["target"] = name
    output["result"] = result.rawValue
    output["valueAfter"] = string(target, kAXValueAttribute)
    if operation[0] == "--set", result == .success,
            string(target, kAXValueAttribute) != operation[2] {
        output["error"] = "AX reported success but value readback does not match"
        emit(output, code: 2)
    }
    emit(output, code: result == .success ? 0 : 2)
}
emit(output, code: traversalErrors.isEmpty ? 0 : 2)

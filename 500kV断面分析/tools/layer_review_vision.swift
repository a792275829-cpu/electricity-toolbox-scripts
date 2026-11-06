import Foundation
import Vision
import AppKit
let file=CommandLine.arguments[1]
let url=URL(fileURLWithPath:file)
let request=VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = false
try VNImageRequestHandler(url:url, options:[:]).perform([request])
let result=(request.results ?? []).compactMap { obs -> [String:Any]? in
 guard let text=obs.topCandidates(1).first else {return nil}
 let b=obs.boundingBox
 return ["text":text.string,"confidence":text.confidence,"x":b.origin.x,"y":b.origin.y,"w":b.width,"h":b.height]
}
let json=try JSONSerialization.data(withJSONObject:result)
print(String(data:json,encoding:.utf8)!)

ObjC.import('Foundation');
ObjC.import('Vision');

function run(argv) {
    var request = $.VNRecognizeTextRequest.alloc.init;
    request.recognitionLevel = $.VNRequestTextRecognitionLevelAccurate;
    request.recognitionLanguages = $(['zh-Hans', 'en-US']);
    request.usesLanguageCorrection = false;
    var handler = $.VNImageRequestHandler.alloc.initWithURLOptions(
        $.NSURL.fileURLWithPath(argv[0]), $.NSDictionary.dictionary
    );
    var error = Ref();
    if (!handler.performRequestsError($([request]), error)) {
        throw new Error(ObjC.unwrap(error[0].localizedDescription));
    }
    var labels = [];
    for (var i = 0; i < request.results.count; i++) {
        var observation = request.results.objectAtIndex(i);
        var candidate = observation.topCandidates(1).objectAtIndex(0);
        var text = ObjC.unwrap(candidate.string);
        // Capacity values always come from the PDF text, never from OCR.
        if (/[\u4e00-\u9fff]/.test(text)) {
            labels.push({text: text, box: observation.boundingBox});
        }
    }
    return JSON.stringify(labels);
}

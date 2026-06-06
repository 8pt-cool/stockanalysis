import Foundation
import Vision
import CoreGraphics
import ImageIO

let args = CommandLine.arguments
guard args.count >= 2 else {
    fputs("usage: vision_ocr.swift image_path\n", stderr)
    exit(2)
}

let imageURL = URL(fileURLWithPath: args[1])
guard let imageSource = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
      let cgImage = CGImageSourceCreateImageAtIndex(imageSource, 0, nil) else {
    fputs("failed to load image\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest { request, error in
    if let error = error {
        fputs("ocr error: \(error)\n", stderr)
        exit(1)
    }
    let observations = (request.results as? [VNRecognizedTextObservation]) ?? []
    let rows = observations.compactMap { observation -> [String: Any]? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return [
            "text": candidate.string,
            "confidence": candidate.confidence,
            "x": box.origin.x,
            "y": box.origin.y,
            "w": box.size.width,
            "h": box.size.height,
        ]
    }
    let data = try! JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}

request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
if #available(macOS 12.0, *) {
    request.revision = VNRecognizeTextRequestRevision3
}
request.recognitionLanguages = ["zh-Hans", "en-US"]

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
} catch {
    fputs("perform failed: \(error)\n", stderr)
    exit(1)
}

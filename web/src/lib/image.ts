/**
 * Client-side photo preparation for AI meal logging (stage 3).
 *
 * The camera hands over a 12-48 MP HEIC/JPEG. We decode it once, draw it onto
 * a canvas no larger than 1280 px on the long side and encode a JPEG (~150-400
 * KB): uploads stay fast on mobile data, the model gets plenty of detail, and
 * the original never leaves the phone. When the user opted into keeping a
 * thumbnail, a 400 px version is made from the same decode.
 *
 * Decoding goes through an <img> element: Safari applies the EXIF orientation
 * when drawing an image to a canvas (iOS 13.4+), so portrait photos stay
 * upright without parsing EXIF ourselves.
 */

export const PHOTO_MAX_PX = 1280
export const THUMB_MAX_PX = 400

function loadImage(file: Blob): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const image = new Image()
    image.decoding = 'async'
    image.onload = () => {
      URL.revokeObjectURL(url)
      resolve(image)
    }
    image.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('image_decode_failed'))
    }
    image.src = url
  })
}

function encode(image: HTMLImageElement, maxPx: number, quality: number): Promise<Blob> {
  const scale = Math.min(1, maxPx / Math.max(image.naturalWidth, image.naturalHeight))
  const width = Math.max(1, Math.round(image.naturalWidth * scale))
  const height = Math.max(1, Math.round(image.naturalHeight * scale))
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d')
  if (!context) return Promise.reject(new Error('canvas_unavailable'))
  context.imageSmoothingQuality = 'high'
  context.drawImage(image, 0, 0, width, height)
  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('encode_failed'))), 'image/jpeg', quality),
  )
}

export interface PreparedPhoto {
  photo: Blob
  thumbnail: Blob | null
  width: number
  height: number
}

export async function preparePhoto(file: Blob, withThumbnail: boolean): Promise<PreparedPhoto> {
  const image = await loadImage(file)
  const photo = await encode(image, PHOTO_MAX_PX, 0.82)
  const thumbnail = withThumbnail ? await encode(image, THUMB_MAX_PX, 0.75) : null
  const scale = Math.min(1, PHOTO_MAX_PX / Math.max(image.naturalWidth, image.naturalHeight))
  return {
    photo,
    thumbnail,
    width: Math.round(image.naturalWidth * scale),
    height: Math.round(image.naturalHeight * scale),
  }
}

// Hash-pinned buffer and bounded input validation, reviewed in the assessment.
constexpr size_t MODEL_BYTES = 52210768;
constexpr uint32_t MODEL_LABELS = 11853;
constexpr size_t MAX_STROKES = 128;
constexpr size_t MAX_STROKE_POINTS = 512;
constexpr size_t MAX_POINTS = 16384;
constexpr size_t MAX_INPUT_BYTES = 2 + 2 * MAX_STROKES + 4 * MAX_POINTS;
constexpr size_t NBEST = 30;
constexpr uint16_t CANVAS_SIZE = 1000;
const char *MODEL_SHA256 = "cd47f16b64e7ecaa4c2813d0f98231dfea7b411f48be1ead5c9b2eeb19330b11";

[[noreturn]] void fail(const char *code) { throw std::runtime_error(code); }
std::vector<char> read_file(const std::filesystem::path& path, size_t maximum, size_t exact = 0) {
  std::ifstream file(path, std::ios::binary | std::ios::ate);
  if (!file) fail("FILE_OPEN");
  const auto end = file.tellg();
  if (end < 0 || static_cast<uint64_t>(end) > maximum ||
      (exact && static_cast<uint64_t>(end) != exact)) fail("FILE_SIZE");
  std::vector<char> bytes(static_cast<size_t>(end));
  file.seekg(0);
  if (!bytes.empty() && !file.read(bytes.data(), static_cast<std::streamsize>(bytes.size())))
    fail("FILE_READ");
  return bytes;
}
uint32_t read_u32(const std::vector<char>& bytes, size_t& cursor) {
  if (cursor > bytes.size() || bytes.size() - cursor < 4) fail("MODEL_TRUNCATED");
  const auto *p = reinterpret_cast<const unsigned char*>(bytes.data() + cursor);
  const uint32_t value = uint32_t(p[0]) | (uint32_t(p[1]) << 8) |
      (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
  cursor += 4;
  return value;
}
float read_float(const std::vector<char>& bytes, size_t& cursor) {
  const uint32_t bits = read_u32(bytes, cursor);
  float value;
  std::memcpy(&value, &bits, 4);
  if (!std::isfinite(value)) fail("MODEL_NONFINITE");
  return value;
}
void validate_model(const std::vector<char>& bytes) {
  static_assert(sizeof(int) == 4, "requires 32-bit native feature indices");
  static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559,
      "requires IEEE754 binary32");
  if (bytes.size() != MODEL_BYTES) fail("MODEL_SIZE");
  if (sha256(bytes) != MODEL_SHA256) fail("MODEL_HASH");
  size_t cursor = 0;
  if ((read_u32(bytes, cursor) ^ 0x0ef71821u) != MODEL_BYTES ||
      read_u32(bytes, cursor) != 1 || read_u32(bytes, cursor) != MODEL_LABELS)
    fail("MODEL_HEADER");
  for (uint32_t label = 0; label < MODEL_LABELS; ++label) {
    if (cursor > bytes.size() || bytes.size() - cursor < 16) fail("MODEL_LABEL_BOUNDS");
    if (!std::memchr(bytes.data() + cursor, '\0', 16) || bytes[cursor] == '\0')
      fail("MODEL_LABEL_VALUE");
    cursor += 16;
    (void)read_float(bytes, cursor);
    // Bounded by remaining verified bytes, with sentinel checked before native parser.
    while (true) {
      const uint32_t index = read_u32(bytes, cursor);
      (void)read_float(bytes, cursor);
      if (index == 0xffffffffu) break;
      if (index > 3000000u) fail("MODEL_FEATURE_INDEX");
    }
  }
  if (cursor != bytes.size()) fail("MODEL_TRAILING_BYTES");
}
uint16_t read_u16(const std::vector<char>& bytes, size_t& cursor) {
  if (cursor > bytes.size() || bytes.size() - cursor < 2) fail("INPUT_TRUNCATED");
  const auto *p = reinterpret_cast<const unsigned char*>(bytes.data() + cursor);
  cursor += 2;
  return uint16_t(p[0]) | (uint16_t(p[1]) << 8);
}
struct Point { uint16_t x, y; };
using Strokes = std::vector<std::vector<Point>>;
Strokes validate_input(const std::vector<char>& bytes) {
  size_t cursor = 0;
  const size_t count = read_u16(bytes, cursor);
  if (!count || count > MAX_STROKES) fail("INPUT_STROKES");
  Strokes strokes;
  strokes.reserve(count);
  size_t total = 0;
  for (size_t sid = 0; sid < count; ++sid) {
    const size_t points = read_u16(bytes, cursor);
    if (!points || points > MAX_STROKE_POINTS || points > MAX_POINTS - total)
      fail("INPUT_POINTS");
    total += points;
    if (cursor > bytes.size() || points > (bytes.size() - cursor) / 4)
      fail("INPUT_TRUNCATED");
    std::vector<Point> stroke;
    stroke.reserve(points);
    for (size_t i = 0; i < points; ++i) {
      const uint16_t x = read_u16(bytes, cursor), y = read_u16(bytes, cursor);
      if (x > CANVAS_SIZE || y > CANVAS_SIZE) fail("INPUT_COORDINATE");
      stroke.push_back({x, y});
    }
    strokes.push_back(std::move(stroke));
  }
  if (cursor != bytes.size()) fail("INPUT_TRAILING_BYTES");
  return strokes;
}

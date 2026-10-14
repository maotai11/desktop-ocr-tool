// Persistent, bounded helper. stdout is protocol only; no native pointers cross it.
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <fcntl.h>
#include <io.h>
#else
#include <openssl/sha.h>
#endif
#include "zinnia.h"

namespace {
std::string sha256(const std::vector<char>& bytes) {
  unsigned char digest[32];
#ifdef _WIN32
  // Windows system API, no separately installed crypto/DLL runtime.
  if (BCryptHash(BCRYPT_SHA256_ALG_HANDLE, nullptr, 0,
      reinterpret_cast<PUCHAR>(const_cast<char*>(bytes.data())),
      static_cast<ULONG>(bytes.size()), digest, sizeof(digest)) < 0)
    throw std::runtime_error("HASH_ERROR");
#else
  if (!SHA256(reinterpret_cast<const unsigned char*>(bytes.data()), bytes.size(), digest))
    throw std::runtime_error("HASH_ERROR");
#endif
  std::string hex;
  const char digits[] = "0123456789abcdef";
  for (unsigned char value : digest) { hex += digits[value >> 4]; hex += digits[value & 15]; }
  return hex;
}
#include "model_and_strokes.h"

constexpr size_t HEADER_BYTES = 20;
constexpr uint8_t START = 1, READY = 2, RECOGNIZE = 3, RESULT = 4, STOP = 5;
struct Frame { uint8_t kind; uint64_t generation; std::vector<char> payload; };
uint64_t unsigned_le(const char* p, size_t count) {
  uint64_t value = 0;
  for (size_t i = 0; i < count; ++i)
    value |= uint64_t(static_cast<unsigned char>(p[i])) << (8 * i);
  return value;
}
void append_le(std::vector<char>& output, uint64_t value, size_t count) {
  for (size_t i = 0; i < count; ++i) output.push_back(static_cast<char>(value >> (8 * i)));
}
bool read_frame(Frame& frame) {
  std::array<char, HEADER_BYTES> header;
  std::cin.read(header.data(), header.size());
  if (std::cin.gcount() == 0 && std::cin.eof()) return false;
  if (std::cin.gcount() != HEADER_BYTES) fail("PROTOCOL_TRUNCATED");
  if (std::memcmp(header.data(), "ZHW1", 4) || header[4] != 1 ||
      unsigned_le(header.data() + 6, 2)) fail("PROTOCOL_HEADER");
  frame.kind = static_cast<uint8_t>(header[5]);
  frame.generation = unsigned_le(header.data() + 8, 8);
  const uint64_t size = unsigned_le(header.data() + 16, 4);
  if (size > MAX_INPUT_BYTES) fail("PROTOCOL_SIZE");
  frame.payload.resize(static_cast<size_t>(size));
  if (size && !std::cin.read(frame.payload.data(), static_cast<std::streamsize>(size)))
    fail("PROTOCOL_TRUNCATED");
  return true;
}
void reply(uint8_t kind, uint64_t generation, const std::vector<char>& payload) {
  if (payload.size() > 204) fail("PROTOCOL_OUTPUT");
  std::vector<char> header{'Z','H','W','1',1,static_cast<char>(kind),0,0};
  append_le(header, generation, 8);
  append_le(header, payload.size(), 4);
  std::cout.write(header.data(), header.size());
  std::cout.write(payload.data(), payload.size());
  std::cout.flush();
  if (!std::cout) fail("PROTOCOL_WRITE");
}
void error_reply(uint8_t kind, uint64_t generation, uint16_t status) {
  std::vector<char> payload;
  append_le(payload, status, 2); append_le(payload, 0, 2);
  reply(kind, generation, payload);
}
std::vector<char> recognize(zinnia::Recognizer& recognizer, const Strokes& strokes) {
  std::unique_ptr<zinnia::Character> character(zinnia::Character::create());
  if (!character) fail("ENGINE_CHARACTER");
  character->set_width(CANVAS_SIZE); character->set_height(CANVAS_SIZE);
  for (size_t sid = 0; sid < strokes.size(); ++sid)
    for (const auto& point : strokes[sid])
      if (!character->add(sid, point.x, point.y)) fail("ENGINE_POINT");
  std::unique_ptr<zinnia::Result> result(recognizer.classify(*character, NBEST));
  if (!result || !result->size() || result->size() > NBEST) fail("ENGINE_RESULT");
  std::vector<char> payload;
  append_le(payload, 0, 2); append_le(payload, result->size(), 2);
  for (size_t i = 0; i < result->size(); ++i) {
    const char* label = result->value(i);
    if (!label) fail("ENGINE_LABEL");
    const char* end = static_cast<const char*>(std::memchr(label, 0, 16));
    if (!end || end == label || !std::isfinite(result->score(i))) fail("ENGINE_OUTPUT");
    const size_t length = static_cast<size_t>(end - label);
    payload.push_back(static_cast<char>(length));
    payload.insert(payload.end(), label, end);  // copy borrowed labels before RAII teardown
    const float score = result->score(i);
    uint32_t bits; std::memcpy(&bits, &score, sizeof(bits));
    append_le(payload, bits, 4);
  }
  return payload;
}
int run(const std::filesystem::path& model_path) {
  static_assert(sizeof(void*) == 8, "64-bit helper required");
  const uint16_t endian = 1;
  if (*reinterpret_cast<const unsigned char*>(&endian) != 1) fail("PLATFORM_ENDIAN");
  Frame input;
  // Parent assigns its kill-on-close Job BEFORE START. EOF here exits cleanly.
  if (!read_frame(input)) return 0;
  if (input.kind != START || input.generation != 0 || !input.payload.empty()) fail("PROTOCOL_START");
  const auto model = read_file(model_path, MODEL_BYTES, MODEL_BYTES);
  validate_model(model);  // exact retained bytes; no TOCTOU native reopen
  std::unique_ptr<zinnia::Recognizer> recognizer(zinnia::Recognizer::create());
  if (!recognizer || !recognizer->open(model.data(), model.size()) ||
      recognizer->size() != MODEL_LABELS) fail("ENGINE_LOAD");
  error_reply(READY, 0, 0);
  uint64_t last_generation = 0;
  while (read_frame(input)) {
    if (input.kind == STOP && input.generation == 0 && input.payload.empty()) return 0;
    if (input.kind != RECOGNIZE || input.generation <= last_generation) fail("PROTOCOL_SEQUENCE");
    last_generation = input.generation;
    Strokes strokes;
    try { strokes = validate_input(input.payload); }
    catch (const std::runtime_error&) { error_reply(RESULT, input.generation, 1); continue; }
    reply(RESULT, input.generation, recognize(*recognizer, strokes));
  }
  return 0;
}
}
#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
  _setmode(_fileno(stdin), _O_BINARY); _setmode(_fileno(stdout), _O_BINARY);
#else
int main(int argc, char** argv) {
#endif
  try {
    if (argc != 2) fail("ARGUMENTS");
    return run(std::filesystem::path(argv[1]));
  } catch (const std::bad_alloc&) { std::cerr << "ALLOCATION_ERROR\n"; return 3; }
    catch (const std::runtime_error& error) { std::cerr << error.what() << '\n'; return 2; }
    catch (...) { std::cerr << "INTERNAL_ERROR\n"; return 3; }
}

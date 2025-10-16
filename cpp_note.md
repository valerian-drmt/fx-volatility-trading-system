# C++ Notes — Enhanced Reference (Advanced) ✨

> A concise, practical, and structured guide to core C++ topics with code examples, tips, and best practices.

---

## 📑 Table of Contents

- [Section 5 — 📦 Preprocessor, Comments, Main & Namespaces](#sec5)
- [Section 6 — 🧮 Sizes, Escape Sequences](#sec6)
- [Section 7 — 📊 Vectors (1D & 2D)](#sec7)
- [Section 8 — ➕ Operators, Casting, Booleans & Logic](#sec8)
- [Section 9 — 🔀 Control Flow (if/switch/loops)](#sec9)
- [Section 10 — 🔤 Characters, C-Strings & `std::string`](#sec10)
- [Section 11 — 🧰 Math, Random, Functions & Overloading](#sec11)
- [Section 12 — 🧭 Pointers, Arrays & `const`-correctness](#sec12)
- [Section 13 — 🧱 OOP Essentials (Classes, Constructors, RAII)](#sec13)
- [Section 17 — ♻️ Smart Pointers](#sec17)
- [Section 23 — 🔖 Enumerations (`enum class`)](#sec23)

---

<a id="sec5"></a>
## ################### Section 5 — 📦 Preprocessor, Comments, Main & Namespaces

<details><summary><strong>Overview</strong></summary>

The preprocessor handles directives like `#include`. Comments document intent. A valid program needs a `main`. Namespaces avoid name collisions (most notably `std`).
</details>

### Includes
```cpp
#include <iostream>   // std::cout, std::cin, std::endl
```

### Comments
```cpp
// Single-line comment
/* Multi-line
   comment */
```

### Main function
```cpp
int main() {
    return 0;
}
```

### Initialization
```cpp
int num2 { /* e.g., num1 - 3 */ };
double dbl {};     // value-initialized to 0.0
```

> 💡 **Tip** — Prefer **brace-initialization** (`{}`) to avoid narrowing and surprises.

### Namespaces & I/O
```cpp
#include <iostream>

int main() {
    using std::cout, std::cin, std::endl;   // selective using-declarations

    int data1{}, data2{};
    cin >> data1 >> data2;
    cout << "Hello! " << "Hey, I'm here" << endl;
    cout << data1 << ' ' << data2 << endl;
}
```

> ⚠️ **Warning** — Avoid `using namespace std;` in headers or global scope of large projects; it can cause name collisions. Prefer qualified names or selective `using`.\
> 💡 **Tip** — Use `'\n'` instead of `std::endl` when you don’t need to flush the stream.

---

<a id="sec6"></a>
## ################### Section 6 — 🧮 Sizes, Escape Sequences

### Sizes with `sizeof`
```cpp
sizeof(int);                 // size of type
sizeof variable;             // size of object
sizeof(42);                  // size of value (type deduced)
```

### Common escape sequences
```
\n  newline
\r  carriage return
\t  tab
\b  backspace
```

> 💡 **Tip** — Prefer `std::size_t` for sizes/indices; it matches the platform’s natural size type.

---

<a id="sec7"></a>
## ################### Section 7 — 📊 Vectors (1D & 2D)

```cpp
#include <vector>
using std::vector;

vector<int> tab {3, 3, 2};
tab.at(2) = 2;            // bounds-checked access
tab.push_back(34);        // -> {3, 3, 2, 34}
auto n = tab.size();      // -> 4

vector<vector<int>> ratings {{0, 0}, {0, 0}}; // 2D vector
int x = ratings[0][0];
```

> 💡 **Tip** — Use `at()` in debug code/learning for safety. Use `[]` in performance-critical paths when you know indices are valid.

---

<a id="sec8"></a>
## ################### Section 8 — ➕ Operators, Casting, Booleans & Logic

### Chained assignment & increment
```cpp
int num1{}, num2{};
num1 = num2 = 100;   // both become 100

int num{};
int lhs = ++num;     // 1) num = num + 1;  2) lhs = num
int rhs = num++;     // 1) rhs = num;      2) num = num + 1
```

### Casting
```cpp
double d = static_cast<double>(num1);   // explicit, safe cast
```

### Conditions & logic
```cpp
bool b = (num1 < num2);
bool ok = (!a) || (a && b);  // !, &&, ||
if (num1 > num2 && num1 > 20) { /* ... */ }
```

> 💡 **Tip** — Prefer `static_cast<T>(x)` to C-style casts.\
> ⚠️ **Warning** — Mind short-circuiting (`&&`/`||`) when expressions have side effects.

---

<a id="sec9"></a>
## ################### Section 9 — 🔀 Control Flow (if/switch/loops)

### `if` / `else if` / `else`
```cpp
if (cond) {
    // statements
} else if (other) {
    // statements
} else {
    // statements
}
```

### `switch`
```cpp
switch (value) {
    case 1: /* statements */ break;
    case 2: /* statements */ /* more */ break;
    default: /* fallback */ ;
}
```
> ℹ️ **Note** — `{}` aren’t required just because multiple statements follow a `case`. They are needed when declaring variables to limit scope or improve readability.

### Ternary
```cpp
auto result = (cond) ? true_expr : false_expr;
```

### `for` loops
```cpp
for (int i{}; i < 10; ++i) { /* ... */ }
for (int i{1}; i < 100; i *= 3) { /* ... */ }

int scores[] {100, 90, 97};
for (int score : scores) { /* copy */ }
for (auto& score : scores) { /* ref */ }
for (char c : std::string{"Bonjour"}) { /* ... */ }
```

### `while` / `do-while`
```cpp
while (cond) { /* ... */ }
do { /* ... */ } while (cond);
```

> 💡 **Tip** — Prefer range-based `for` (`for (auto& x : container)`) when iterating containers.\
> ⚠️ **Warning** — Don’t modify a container while range-iterating it unless you know the iterator invalidation rules.

---

<a id="sec10"></a>
## ################### Section 10 — 🔤 Characters, C-Strings & `std::string`

### Character classification (`<cctype>`)
```cpp
isalpha(c);  isalnum(c);  isdigit(c);
isupper(c);  islower(c);  isspace(c);
ispunct(c);  isxdigit(c); isgraph(c);
isprint(c);  iscntrl(c);
```

### C-Strings (`<cstring>`)

```cpp
char name[20]{};
// name = "Oui";         // ❌ cannot assign to array
std::strcpy(name, "Oui");     // ✅ copy
std::strcat(name, " ther");   // ✅ concatenate
std::strlen(name);            // length
std::strcmp(name, "Another"); // 0 if equal
```

> ⚠️ **Warning** — C-strings are error-prone (buffers, null-termination). Prefer `std::string` unless you **must** use C APIs.

### `std::string`
```cpp
#include <string>
using std::string;

string s1;                 // empty
string s2{"Frank"};        // "Frank"
string s3{s2};             // copy
string s4{"Frank", 3};     // "Fra"
string s5{s3, 0, 2};       // "Fr"
string s6(3, 'X');         // "XXX"

string sentence = s1 + s2; // concatenation

bool eq = (s1 == s2);
bool gt = (s2 > s3);

string s{"This is a test"};
s.substr(0, 4);            // "This"
s.find("This");            // 0
s.erase(5, 2);             // remove 2 chars from index 5
s.clear();                 // make empty
auto n = s.length();       // size_t
char c0 = s.at(0);         // bounds-checked
char c1 = s[0];            // unchecked

std::getline(std::cin, s);        // read line
std::getline(std::cin, s, 'x');   // read until 'x' (discarded)
for (std::size_t i{}; i < s.length(); ++i) { /* ... */ }

constexpr auto npos = std::string::npos;
```

> 💡 **Tip** — Use `s.reserve()` when you know the target size to minimize allocations.\
> 💡 **Tip** — Prefer `s.at(i)` while learning/debugging; switch to `operator[]` once safe.

---

<a id="sec11"></a>
## ################### Section 11 — 🧰 Math, Random, Functions & Overloading

### `<cmath>`
```cpp
#include <cmath>
std::sqrt(400);
std::pow(2, 3);
std::cbrt(x);
std::sin(x);
std::cos(x);
```

### Functions
```cpp
int add(int a, int b) {
    return a + b;
}

void func(int a, std::string b) {
    // ...
}
```

### Overloading
```cpp
double add(double a, double b);
int    add(int a, int b);
```

### Passing arrays
```cpp
void print_array(const int numbers[], std::size_t size);
```

### Random (`<cstdlib>` + `<ctime>`) — *legacy approach*
```cpp
#include <cstdlib>
#include <ctime>

std::srand(static_cast<unsigned>(std::time(nullptr)));
int r = std::rand();   // legacy RNG
```

> 💡 **Tip** — Prefer `<random>` (Mersenne Twister, distributions) in modern C++.

### Pass-by-reference
```cpp
void scale_number(int& num) {  // modifies caller's variable
    num = 100;
}

int main() {
    int number{1000};
    scale_number(number);
    std::cout << number << '\n';   // 100
}
```

### `static`
- **Function-local**: `static int num{1000};` — persists across calls.
- **Class**: `static int count;` — shared across instances.
- **Namespace/file**: `static` at global scope gives **internal linkage** (file visibility only).

### `inline`
```cpp
inline int square(int x) { return x * x; }
int main() {
    std::cout << square(5) << '\n';
}
```

> ⚠️ **Warning** — `inline` is a **linkage** suggestion (ODR control), not a guaranteed inlining hint. Compilers decide inlining independently.

---

<a id="sec12"></a>
## ################### Section 12 — 🧭 Pointers, Arrays & `const`-correctness

### Basics
```cpp
int* int_ptr{nullptr};
std::string* string_ptr{nullptr};

int value{10};
int_ptr = &value;          // address of value

std::cout << sizeof value << '\n';
```

### Dereference & modify
```cpp
int score{100};
int* score_ptr{&score};

std::cout << *score_ptr << '\n';   // 100
*score_ptr = 200;
std::cout << *score_ptr << '\n';   // 200
std::cout << score << '\n';        // 200
```

> ⚠️ **Warning** — Distinguish **pointer declaration** (`int* p`) from **dereference** (`*p`).\
> 💡 **Tip** — Prefer `nullptr` over `NULL` or `0` for null pointers.

### Deallocation
```cpp
int* p = new int{42};
delete p;        // free allocated storage
p = nullptr;     // clear dangling pointer
```

### Arrays & pointer arithmetic
```cpp
int arr[] {1, 2, 3, 4, 5};     // static array
int* p = arr;                   // points to first element

arr[0] == p[0] && arr[1] == *(p + 1);

++p; --p; p += 2; p -= 1;
std::ptrdiff_t n = p - arr;    // distance
```

### `const` correctness with pointers
```cpp
const int* p1 = &value;   // pointer to const int (data const) — you can't modify *p1
int* const p2 = &value;   // const pointer to int (address const) — you can't rebind p2
const int* const p3 = &value; // both data and address const
```

> 💡 **Tip** — “`const` on the left of `*` protects the data; on the right protects the pointer.”

### Pointers in functions
```cpp
void double_data(int* p) { *p *= 2; }

int main() {
    int v = 10;
    double_data(&v);   // v becomes 20
}
```

### Returning pointers *(avoid owning raw pointers)*
```cpp
int* passthrough(int* ptr) { return ptr; }  // returns same pointer
```

### Range loops by reference
```cpp
std::vector<std::string> stooges {"Larry", "Moe", "Curly"};
for (const auto& name : stooges) {
    // no copy; cannot modify
}
```

> ⚠️ **Warning** — Don’t return pointers/references to **locals** (dangling). Prefer smart pointers or values.

---

<a id="sec13"></a>
## ################### Section 13 — 🧱 OOP Essentials (Classes, Constructors, RAII)

### What is OOP?
Object-Oriented Programming organizes code around **objects** that combine **data (members)** and **behavior (methods)** to improve structure, reuse, and modeling.

**Four principles**: Encapsulation, Abstraction, Inheritance, Polymorphism.

### Basic class
```cpp
#include <iostream>
#include <string>

class Player {
private:
    std::string name;
    int health;
    int xp;

public:
    Player(std::string n, int h, int x) : name{std::move(n)}, health{h}, xp{x} {}

    void talk(const std::string& text) const {
        std::cout << name << " says: " << text << '\n';
    }

    bool is_alive() const { return health > 0; }
};
```

### Creating objects
```cpp
int main() {
    // Stack
    Player frank{"Frank", 100, 10};
    Player hero{"Hero", 150, 20};

    // Heap (avoid raw new; prefer smart pointers)
    Player* enemy = new Player{"Enemy", 120, 15};
    frank.talk("Hello!");
    hero.talk("Let's go!");
    enemy->talk("I'm the enemy!");

    delete enemy; // required for raw new (avoid)
}
```

### Constructors / Destructor
```cpp
class Player {
public:
    std::string name{"None"};
    int health{0};
    int xp{0};

    Player() = default;
    Player(std::string n, int h, int x) : name{std::move(n)}, health{h}, xp{x} {}
    ~Player() { std::cout << "Destructor: " << name << '\n'; }
};
```

> 💡 **Tip** — Use **member initializer lists**; they’re efficient and needed for const/reference members.\
> ⚠️ **Warning** — When you `new`, you must `delete`. Prefer RAII and smart pointers (`std::unique_ptr`, `std::shared_ptr`).

### Best practices
- Prefer **stack allocation** (automatic lifetime) when possible.
- Mark methods `const` when they don’t modify the object.
- Follow the **Rule of 0/3/5** for special members.
- Use **smart pointers** to manage dynamic resources safely.

---

<a id="sec17"></a>
## ################### Section 17 — ♻️ Smart Pointers

```cpp
#include <memory>
#include <iostream>

void demo_unique() {
    std::unique_ptr<int> ptr = std::make_unique<int>(42);
    std::cout << "Value: " << *ptr << '\n';
} // memory freed automatically
```

> 💡 **Tip** — `std::unique_ptr` expresses **unique ownership**. Use `std::shared_ptr` only when shared ownership is required; prefer `std::weak_ptr` to break cycles.

---

<a id="sec23"></a>
## ################### Section 23 — 🔖 Enumerations (`enum class`)

```cpp
enum class Status { OK = 200, NOT_FOUND = 404 };

Status s = Status::NOT_FOUND;
s = Status::OK;   // ✅

// s = OK;  // ❌ error (scoped)
// s = ok;  // ❌ error (case-sensitive & undeclared)
```

> 💡 **Tip** — Use `enum class` (scoped enums) to avoid name pollution and ensure strong typing. Cast with `static_cast<int>(s)` when needed.

---

## ✅ Quick Reference Cheats

> 💡 **Casting** — `static_cast<T>(x)` for safe, explicit conversions.  
> ⚠️ **Raw `new`/`delete`** — Avoid. Prefer RAII & smart pointers.  
> 💡 **Ranges** — `for (auto& x : container)` for simple iteration.  
> 💡 **I/O newline** — Use `'\n'` (fast) vs. `std::endl` (flush + newline).  
> ⚠️ **C-strings** — Prefer `std::string` unless interacting with C APIs.

---

_© Your personal study sheet — optimized for clarity, safety, and modern C++ practices._

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

<details><summary><strong>Overview</strong></summary>

Object-Oriented Programming (OOP) organizes code around **objects** — instances of **classes** that bundle **data (attributes)** and **behavior (methods)**.  
It enhances modularity, reusability, and encapsulation.  
Key concepts: **Encapsulation**, **Abstraction**, **Inheritance**, and **Polymorphism**.
</details>

### Classes & Objects
```cpp
class Player {
private:
    std::string name;
    int health;
    int xp;

public:
    void talk(const std::string& msg) const {
        std::cout << name << " says: " << msg << '\n';
    }
};
```
- **Class**: blueprint defining structure and behavior.  
- **Object**: instance of a class.

```cpp
Player frank;     // object on stack
Player* enemy = new Player;  // on heap
```

> 💡 **Tip** — Avoid `new` when possible; prefer stack or smart pointers.

### Access Modifiers
- `public`: accessible everywhere.  
- `private`: accessible only within the class.  
- `protected`: accessible by derived classes.

```cpp
class Example {
private:
    int hidden;
public:
    void set_hidden(int v) { hidden = v; }
    int get_hidden() const { return hidden; }
};
```

### Member Methods
Methods can manipulate private attributes:
```cpp
class Account {
private:
    double balance;
public:
    void deposit(double amount) { balance += amount; }
    void withdraw(double amount) { balance -= amount; }
};
```

### Constructors & Destructors
Special functions for initialization and cleanup:
```cpp
class Player {
public:
    Player();                           // Default constructor
    Player(std::string n, int h);       // Overloaded
    ~Player();                          // Destructor
};
```

```cpp
Player::Player() : name{"None"}, health{0}, xp{0} {}
Player::Player(std::string n, int h) : name{std::move(n)}, health{h}, xp{0} {}
Player::~Player() { std::cout << "Destroyed " << name << '\n'; }
```

> 💡 **Tip** — Use **initializer lists** for efficiency and to initialize const/reference members.

### Copy & Move Constructors
```cpp
Player(const Player& src);     // Copy
Player(Player&& src) noexcept; // Move
```

- **Copy** duplicates data (deep or shallow).  
- **Move** transfers ownership of resources.

```cpp
Player::Player(Player&& src) noexcept
    : name{std::move(src.name)}, health{src.health}, xp{src.xp} {
    src.health = src.xp = 0;
}
```

### The `this` Pointer
Refers to the current object:
```cpp
void set_health(int health) { this->health = health; }
```

### `const` Methods
Prevent modification of the object:
```cpp
int get_xp() const { return xp; }  // read-only
```

### Static Members
Shared by all instances:
```cpp
class Player {
public:
    static int num_players;
};
int Player::num_players = 0;
```

### Structs vs Classes
`struct` defaults to `public`, `class` defaults to `private`.  
Otherwise identical in C++.

### Friend Functions
Allow external access to private data:
```cpp
class Player {
    friend void display(const Player&);
};
```

### Section Challenge
Implement a `Player` class with multiple constructors, including copy and move semantics, public accessors, and destructor messages for lifetime tracking.


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

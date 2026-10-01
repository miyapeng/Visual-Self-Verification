# Exact Vision2Web experiment prompts

These are the complete task prompts supplied to OpenHands and Claude Code. Tool schemas are not task-prompt text. `official` and `browser_enabled` are byte-identical by design; `guided_vsv` appends the frozen detailed verification procedure. For Claude Code, `browser_enabled` is not an active condition because the official scaffold already exposes playwright-cli.

## Level 1 / `webpage` / `official`

SHA-256: `0ec0f6a4465ad81e14fc67290ed2c1a077fe150715d0b818ef113db7c9159b34`

````text
You are a **senior front-end engineer with extensive experience in webpage development**.

Your responsibility is to **methodically implement and deploy a complete, production-ready static single-page website** strictly in accordance with the provided materials in the current working directory.
You are required to **follow the instructions continuously until the website is fully operational**.
**No clarifications or approvals should be sought, and no steps should be omitted prematurely.**

---

## I. Input Materials

The development artifacts provided for this task include:

1. **Prototype Images**
   - Location: `/workspace/prototypes/*.jpg`
   - Purpose:
     - Define page layout and UI structure
     - Specify visual style, hierarchy, and aesthetics
     - Illustrate interaction behaviors and UI states
     - Serve as the **definitive reference** for visual fidelity

2. **Resource Files**
   - Location: `/workspace/resources/**/*`
   - Includes images, videos, icons, fonts, and other assets
   - Must be utilized wherever relevant to faithfully reproduce the prototype content

3. **Resolution Information**
   - 1920x1,080 pixels (desktop)
   - 1024x768 pixels (tablet)
   - 375x812 pixels (mobile)

---

## II. Mandatory Development Workflow

The following workflow must be **strictly adhered to in the specified order**.

### Step 1: Implementation

#### Static Website Development
- **Strictly replicate prototype visuals**, including:
  - Exact layout and spacing measurements
  - Typography (fonts, sizes, weights, line heights)
  - Color scheme (exact color codes)
  - Visual hierarchy and element positioning

- **Use actual resource files** from `/workspace/resources/`:
  - Reference images using relative paths
  - Embed or link fonts appropriately
  - Include all icons and graphical elements

- **Ensure responsive design**:
  - Mobile, tablet, and desktop layouts
  - Breakpoints and media queries
  - Touch-friendly interactions

- **Optimize for performance**:
  - Minimize CSS and JavaScript
  - Optimize image loading
  - Ensure fast page load times

---

### Step 2: Deployment, Verification, and Script Generation

1. Test the website locally to verify:
   - The website is accessible at `http://localhost:3000`
   - All sections display correctly and match prototypes pixel-perfectly
   - All interactive elements function as expected
   - Resource files load correctly
   - Responsive behavior works (if applicable)
2. Generate a **deployment script `/workspace/start.sh`** with the following requirements:
   - The script must be **fully self-contained**, such that it can be run inside a **completely new container** with no prior dependencies
   - Prepare the full environment: install all runtime dependencies, build tools, and required system packages.
   - Start the development server
   - After running:
     ```bash
     bash /workspace/start.sh
     ```
     the website must be **fully operational and accessible** at
     **`http://localhost:3000`**, serving all static HTML/CSS/JS files and resources.
   Note: The application only needs to run in development mode. There is no need to use production configurations.
3. **Verify the deployment script**:
   - Run `bash /workspace/start.sh` in a clean environment
   - Confirm the website loads at `http://localhost:3000`

---

### Step 3: Documentation

Produce the following documentation:

1. **README.md**
   - Project overview and purpose
   - Technology stack and dependencies
   - Directory structure explanation
   - Local deployment instructions (step-by-step)
   - How to use `start.sh` to start the server
   - Browser compatibility notes
   - Known limitations (if any)

---

## III. Required Deliverables

Deliverables include:

1. Complete static website source code (HTML, CSS, JavaScript)
2. All assets properly organized with relative paths
3. `/workspace/start.sh` deployment script (tested and verified)
4. README.md with complete deployment instructions

> Requirement: Place all materials under the /workspace directory.

---

## IV. Hard Constraints

- Do **not** assume unspecified requirements
- Do **not** skip any feature or section visible in prototypes
- Do **not** merge, remove, or invent sections not shown in prototypes
- **The system must be fully reproducible by running `bash /workspace/start.sh` in a clean container**
- **The website must be accessible at exactly `http://localhost:3000` after deployment**
````

## Level 1 / `webpage` / `browser_enabled`

SHA-256: `0ec0f6a4465ad81e14fc67290ed2c1a077fe150715d0b818ef113db7c9159b34`

````text
You are a **senior front-end engineer with extensive experience in webpage development**.

Your responsibility is to **methodically implement and deploy a complete, production-ready static single-page website** strictly in accordance with the provided materials in the current working directory.
You are required to **follow the instructions continuously until the website is fully operational**.
**No clarifications or approvals should be sought, and no steps should be omitted prematurely.**

---

## I. Input Materials

The development artifacts provided for this task include:

1. **Prototype Images**
   - Location: `/workspace/prototypes/*.jpg`
   - Purpose:
     - Define page layout and UI structure
     - Specify visual style, hierarchy, and aesthetics
     - Illustrate interaction behaviors and UI states
     - Serve as the **definitive reference** for visual fidelity

2. **Resource Files**
   - Location: `/workspace/resources/**/*`
   - Includes images, videos, icons, fonts, and other assets
   - Must be utilized wherever relevant to faithfully reproduce the prototype content

3. **Resolution Information**
   - 1920x1,080 pixels (desktop)
   - 1024x768 pixels (tablet)
   - 375x812 pixels (mobile)

---

## II. Mandatory Development Workflow

The following workflow must be **strictly adhered to in the specified order**.

### Step 1: Implementation

#### Static Website Development
- **Strictly replicate prototype visuals**, including:
  - Exact layout and spacing measurements
  - Typography (fonts, sizes, weights, line heights)
  - Color scheme (exact color codes)
  - Visual hierarchy and element positioning

- **Use actual resource files** from `/workspace/resources/`:
  - Reference images using relative paths
  - Embed or link fonts appropriately
  - Include all icons and graphical elements

- **Ensure responsive design**:
  - Mobile, tablet, and desktop layouts
  - Breakpoints and media queries
  - Touch-friendly interactions

- **Optimize for performance**:
  - Minimize CSS and JavaScript
  - Optimize image loading
  - Ensure fast page load times

---

### Step 2: Deployment, Verification, and Script Generation

1. Test the website locally to verify:
   - The website is accessible at `http://localhost:3000`
   - All sections display correctly and match prototypes pixel-perfectly
   - All interactive elements function as expected
   - Resource files load correctly
   - Responsive behavior works (if applicable)
2. Generate a **deployment script `/workspace/start.sh`** with the following requirements:
   - The script must be **fully self-contained**, such that it can be run inside a **completely new container** with no prior dependencies
   - Prepare the full environment: install all runtime dependencies, build tools, and required system packages.
   - Start the development server
   - After running:
     ```bash
     bash /workspace/start.sh
     ```
     the website must be **fully operational and accessible** at
     **`http://localhost:3000`**, serving all static HTML/CSS/JS files and resources.
   Note: The application only needs to run in development mode. There is no need to use production configurations.
3. **Verify the deployment script**:
   - Run `bash /workspace/start.sh` in a clean environment
   - Confirm the website loads at `http://localhost:3000`

---

### Step 3: Documentation

Produce the following documentation:

1. **README.md**
   - Project overview and purpose
   - Technology stack and dependencies
   - Directory structure explanation
   - Local deployment instructions (step-by-step)
   - How to use `start.sh` to start the server
   - Browser compatibility notes
   - Known limitations (if any)

---

## III. Required Deliverables

Deliverables include:

1. Complete static website source code (HTML, CSS, JavaScript)
2. All assets properly organized with relative paths
3. `/workspace/start.sh` deployment script (tested and verified)
4. README.md with complete deployment instructions

> Requirement: Place all materials under the /workspace directory.

---

## IV. Hard Constraints

- Do **not** assume unspecified requirements
- Do **not** skip any feature or section visible in prototypes
- Do **not** merge, remove, or invent sections not shown in prototypes
- **The system must be fully reproducible by running `bash /workspace/start.sh` in a clean container**
- **The website must be accessible at exactly `http://localhost:3000` after deployment**
````

## Level 1 / `webpage` / `guided_vsv`

SHA-256: `3d9b9f45dc4cd196d322026882ceef1a912c44f5dc8ee78ddeb8cca3445fff4e`

````text
You are a **senior front-end engineer with extensive experience in webpage development**.

Your responsibility is to **methodically implement and deploy a complete, production-ready static single-page website** strictly in accordance with the provided materials in the current working directory.
You are required to **follow the instructions continuously until the website is fully operational**.
**No clarifications or approvals should be sought, and no steps should be omitted prematurely.**

---

## I. Input Materials

The development artifacts provided for this task include:

1. **Prototype Images**
   - Location: `/workspace/prototypes/*.jpg`
   - Purpose:
     - Define page layout and UI structure
     - Specify visual style, hierarchy, and aesthetics
     - Illustrate interaction behaviors and UI states
     - Serve as the **definitive reference** for visual fidelity

2. **Resource Files**
   - Location: `/workspace/resources/**/*`
   - Includes images, videos, icons, fonts, and other assets
   - Must be utilized wherever relevant to faithfully reproduce the prototype content

3. **Resolution Information**
   - 1920x1,080 pixels (desktop)
   - 1024x768 pixels (tablet)
   - 375x812 pixels (mobile)

---

## II. Mandatory Development Workflow

The following workflow must be **strictly adhered to in the specified order**.

### Step 1: Implementation

#### Static Website Development
- **Strictly replicate prototype visuals**, including:
  - Exact layout and spacing measurements
  - Typography (fonts, sizes, weights, line heights)
  - Color scheme (exact color codes)
  - Visual hierarchy and element positioning

- **Use actual resource files** from `/workspace/resources/`:
  - Reference images using relative paths
  - Embed or link fonts appropriately
  - Include all icons and graphical elements

- **Ensure responsive design**:
  - Mobile, tablet, and desktop layouts
  - Breakpoints and media queries
  - Touch-friendly interactions

- **Optimize for performance**:
  - Minimize CSS and JavaScript
  - Optimize image loading
  - Ensure fast page load times

---

### Step 2: Deployment, Verification, and Script Generation

1. Test the website locally to verify:
   - The website is accessible at `http://localhost:3000`
   - All sections display correctly and match prototypes pixel-perfectly
   - All interactive elements function as expected
   - Resource files load correctly
   - Responsive behavior works (if applicable)
2. Generate a **deployment script `/workspace/start.sh`** with the following requirements:
   - The script must be **fully self-contained**, such that it can be run inside a **completely new container** with no prior dependencies
   - Prepare the full environment: install all runtime dependencies, build tools, and required system packages.
   - Start the development server
   - After running:
     ```bash
     bash /workspace/start.sh
     ```
     the website must be **fully operational and accessible** at
     **`http://localhost:3000`**, serving all static HTML/CSS/JS files and resources.
   Note: The application only needs to run in development mode. There is no need to use production configurations.
3. **Verify the deployment script**:
   - Run `bash /workspace/start.sh` in a clean environment
   - Confirm the website loads at `http://localhost:3000`

---

### Step 3: Documentation

Produce the following documentation:

1. **README.md**
   - Project overview and purpose
   - Technology stack and dependencies
   - Directory structure explanation
   - Local deployment instructions (step-by-step)
   - How to use `start.sh` to start the server
   - Browser compatibility notes
   - Known limitations (if any)

---

## III. Required Deliverables

Deliverables include:

1. Complete static website source code (HTML, CSS, JavaScript)
2. All assets properly organized with relative paths
3. `/workspace/start.sh` deployment script (tested and verified)
4. README.md with complete deployment instructions

> Requirement: Place all materials under the /workspace directory.

---

## IV. Hard Constraints

- Do **not** assume unspecified requirements
- Do **not** skip any feature or section visible in prototypes
- Do **not** merge, remove, or invent sections not shown in prototypes
- **The system must be fully reproducible by running `bash /workspace/start.sh` in a clean container**
- **The website must be accessible at exactly `http://localhost:3000` after deployment**

Before final submission, perform a concrete visual self-verification of the application you implemented. Use only the public task requirements, PRD, prototype images, resources, current source code, and observations produced by running your own application.

Follow this procedure after you have obtained the first runnable implementation:

1. Establish the deployed version. Inspect the existing /workspace/start.sh and use it to launch the application at http://localhost:3000. Run it in the background when continued terminal work is needed, wait until the URL responds, and inspect its launch log if startup fails. Do not replace a valid start.sh with an unrelated server command merely for verification.
2. Observe the real application. Open http://localhost:3000 with the browser interface available in the current coding scaffold and inspect the rendered initial state. Make sure actual screenshot pixels enter your context; a screenshot path alone, curl response, raw HTML, HTTP 200, or viewing the provided prototype does not count as observing the implementation.
3. Verify one user-observable requirement at a time. Do not create or execute one whole-site mega-plan. For each check, first state a concise specification with: (a) the functional objective, (b) the expected observable outcome, (c) the reset or starting condition, and (d) a short complete sequence of semantic actions. Actions may include navigate, click, fill, select, hover, press, scroll, or go back. Include only the prerequisites needed for that objective.
4. Execute the complete action sequence using the available browser interface. Inspect the returned step-by-step screenshots, current URL and page state, DOM or accessibility changes, console/runtime errors, and action failures. The browser only executes and records actions; you must decide whether the expected outcome holds.
5. Use evidence before editing. If the observed application conflicts with the requirement or prototype, inspect the responsible code and make the smallest coherent correction. Do not change code solely because you expected a failure, and do not weaken the requirement or expected outcome after seeing the result.
6. Recheck meaningful changes. Ensure the updated code is actually deployed, replay the failed check, and confirm that the observed failure is removed. Also recheck a small, relevant sample of previously successful behavior that the change could affect so that a visual repair does not introduce a functional or visual regression.
7. Continue or stop deliberately. You may perform additional function-scoped checks when they are useful. You decide which requirements to inspect, how many checks to run, whether more editing is necessary, and when the implementation is ready to submit.
````

## Level 2 / `frontend` / `official`

SHA-256: `b8c72e84c9efd85cba8ada58c0a527e69c771776f0a2a35296bede0317da3c9f`

````text
You are a **senior front-end engineer with extensive experience in interactive frontend development**.

Your responsibility is to **methodically implement and deploy a complete, interactive front-end application** strictly in accordance with the provided materials in the current working directory.  
You are required to **follow the instructions continuously until the application is fully operational**.  
**No clarifications or approvals should be sought, and no steps should be omitted prematurely.**

---

## I. Input Materials

The development artifacts provided for this task include:

1. **Task Description**
   - Location: `/workspace/prompt.txt`
   - Contains the specific requirements and functionality to implement

2. **Prototype Images**
   - Location: `/workspace/prototypes/*.jpg`
   - Purpose:
     - Define page layout and UI structure
     - Specify visual style, hierarchy, and aesthetics
     - Illustrate interaction behaviors and UI states
     - Serve as the **definitive reference** for visual fidelity

3. **Resource Files**  
   - Location: `/workspace/resources/**/*`  
   - Includes images, videos, audio, icons, and other assets
   - Must be utilized wherever relevant to faithfully reproduce the prototype content

---

## II. Mandatory Development Workflow

The following workflow must be **strictly adhered to in the specified order**.

### Step 1: Planning and Design

Produce a **comprehensive design document** encompassing:

#### 1. Application Architecture
- Define component hierarchy
- Plan routing

#### 2. Technology Stack Selection
Explicitly specify the following:
- Front-end framework (React, Vue, etc.)
- UI component library (if any)
- Build and development tooling

#### 3. Project Directory Structure
- Present a clear, production-grade folder organization
- Organize components, assets, and utilities properly

---

### Step 2: Implementation

#### Front-End
- **Strictly replicate prototype visuals**, including:
  - Layout and spacing
  - Typography
  - Color scheme
  - Visual hierarchy
- Implement **all interactions and functionality** as specified in `/workspace/prompt.txt`
- Use actual resource files from `/workspace/resources/`
- Implement proper state management
- Support **all UI states**
- Ensure responsive design if indicated by prototypes
- **In this task, Frontend code may directly include mock data to faithfully reproduce the prototype states and visual presentation**

---

### Step 3: Deployment, Verification, and Script Generation

1. Build and test the application locally to verify:
   - The application is accessible at `http://localhost:3000`
   - All features and interactions work correctly
   - Visuals exactly match the prototype images
2. Generate a **deployment script `/workspace/start.sh`** with the following requirements:
   - The script must be fully self-contained, such that it can be run inside a completely new container with no prior dependencies.
   - Prepare the full environment: install all runtime dependencies, build tools, and required system packages.
   - Start the development server
   - After running:
     ```bash
     bash /workspace/start.sh
     ```
     the application must be fully operational and accessible at  
     **`http://localhost:3000`**, with all features working correctly.
     Note: The application only needs to run in development mode. There is no need to use production configurations.
3. Run start.sh in a clean environment
   - Verify that the script successfully installs dependencies and launches the application.
   - Confirm that the website is fully operational and accessible at http://localhost:3000

---

### Step 4: Documentation

Produce the following documentation:

1. **Design Document**
   - Application architecture
   - Component structure
   - Technology stack
   - Implementation details

2. **README.md**
   - Project overview
   - Technology stack
   - Directory structure
   - Local deployment instructions
   - How to use `start.sh` to start the application
   - Feature list

---

## III. Required Deliverables

Deliverables include:

1. Complete front-end application source code
2. Design document
3. All assets properly organized
4. `/workspace/start.sh` deployment script
5. README with deployment instructions

> Requirement: Place all materials under the /workspace directory.

---

## IV. Hard Constraints

- Do **not** assume unspecified requirements  
- Do **not** skip any required feature from `/workspace/prompt.txt`
- Do **not** merge, remove, or invent features  
- **All features must work correctly and visuals must match the prototypes**  
- **The system must be fully reproducible by running `bash /workspace/start.sh`**
````

## Level 2 / `frontend` / `browser_enabled`

SHA-256: `b8c72e84c9efd85cba8ada58c0a527e69c771776f0a2a35296bede0317da3c9f`

````text
You are a **senior front-end engineer with extensive experience in interactive frontend development**.

Your responsibility is to **methodically implement and deploy a complete, interactive front-end application** strictly in accordance with the provided materials in the current working directory.  
You are required to **follow the instructions continuously until the application is fully operational**.  
**No clarifications or approvals should be sought, and no steps should be omitted prematurely.**

---

## I. Input Materials

The development artifacts provided for this task include:

1. **Task Description**
   - Location: `/workspace/prompt.txt`
   - Contains the specific requirements and functionality to implement

2. **Prototype Images**
   - Location: `/workspace/prototypes/*.jpg`
   - Purpose:
     - Define page layout and UI structure
     - Specify visual style, hierarchy, and aesthetics
     - Illustrate interaction behaviors and UI states
     - Serve as the **definitive reference** for visual fidelity

3. **Resource Files**  
   - Location: `/workspace/resources/**/*`  
   - Includes images, videos, audio, icons, and other assets
   - Must be utilized wherever relevant to faithfully reproduce the prototype content

---

## II. Mandatory Development Workflow

The following workflow must be **strictly adhered to in the specified order**.

### Step 1: Planning and Design

Produce a **comprehensive design document** encompassing:

#### 1. Application Architecture
- Define component hierarchy
- Plan routing

#### 2. Technology Stack Selection
Explicitly specify the following:
- Front-end framework (React, Vue, etc.)
- UI component library (if any)
- Build and development tooling

#### 3. Project Directory Structure
- Present a clear, production-grade folder organization
- Organize components, assets, and utilities properly

---

### Step 2: Implementation

#### Front-End
- **Strictly replicate prototype visuals**, including:
  - Layout and spacing
  - Typography
  - Color scheme
  - Visual hierarchy
- Implement **all interactions and functionality** as specified in `/workspace/prompt.txt`
- Use actual resource files from `/workspace/resources/`
- Implement proper state management
- Support **all UI states**
- Ensure responsive design if indicated by prototypes
- **In this task, Frontend code may directly include mock data to faithfully reproduce the prototype states and visual presentation**

---

### Step 3: Deployment, Verification, and Script Generation

1. Build and test the application locally to verify:
   - The application is accessible at `http://localhost:3000`
   - All features and interactions work correctly
   - Visuals exactly match the prototype images
2. Generate a **deployment script `/workspace/start.sh`** with the following requirements:
   - The script must be fully self-contained, such that it can be run inside a completely new container with no prior dependencies.
   - Prepare the full environment: install all runtime dependencies, build tools, and required system packages.
   - Start the development server
   - After running:
     ```bash
     bash /workspace/start.sh
     ```
     the application must be fully operational and accessible at  
     **`http://localhost:3000`**, with all features working correctly.
     Note: The application only needs to run in development mode. There is no need to use production configurations.
3. Run start.sh in a clean environment
   - Verify that the script successfully installs dependencies and launches the application.
   - Confirm that the website is fully operational and accessible at http://localhost:3000

---

### Step 4: Documentation

Produce the following documentation:

1. **Design Document**
   - Application architecture
   - Component structure
   - Technology stack
   - Implementation details

2. **README.md**
   - Project overview
   - Technology stack
   - Directory structure
   - Local deployment instructions
   - How to use `start.sh` to start the application
   - Feature list

---

## III. Required Deliverables

Deliverables include:

1. Complete front-end application source code
2. Design document
3. All assets properly organized
4. `/workspace/start.sh` deployment script
5. README with deployment instructions

> Requirement: Place all materials under the /workspace directory.

---

## IV. Hard Constraints

- Do **not** assume unspecified requirements  
- Do **not** skip any required feature from `/workspace/prompt.txt`
- Do **not** merge, remove, or invent features  
- **All features must work correctly and visuals must match the prototypes**  
- **The system must be fully reproducible by running `bash /workspace/start.sh`**
````

## Level 2 / `frontend` / `guided_vsv`

SHA-256: `e614f7b27cef563b027d2c5ab448eba4ef020d34fe770fdf5962db7b6c3b5c5f`

````text
You are a **senior front-end engineer with extensive experience in interactive frontend development**.

Your responsibility is to **methodically implement and deploy a complete, interactive front-end application** strictly in accordance with the provided materials in the current working directory.  
You are required to **follow the instructions continuously until the application is fully operational**.  
**No clarifications or approvals should be sought, and no steps should be omitted prematurely.**

---

## I. Input Materials

The development artifacts provided for this task include:

1. **Task Description**
   - Location: `/workspace/prompt.txt`
   - Contains the specific requirements and functionality to implement

2. **Prototype Images**
   - Location: `/workspace/prototypes/*.jpg`
   - Purpose:
     - Define page layout and UI structure
     - Specify visual style, hierarchy, and aesthetics
     - Illustrate interaction behaviors and UI states
     - Serve as the **definitive reference** for visual fidelity

3. **Resource Files**  
   - Location: `/workspace/resources/**/*`  
   - Includes images, videos, audio, icons, and other assets
   - Must be utilized wherever relevant to faithfully reproduce the prototype content

---

## II. Mandatory Development Workflow

The following workflow must be **strictly adhered to in the specified order**.

### Step 1: Planning and Design

Produce a **comprehensive design document** encompassing:

#### 1. Application Architecture
- Define component hierarchy
- Plan routing

#### 2. Technology Stack Selection
Explicitly specify the following:
- Front-end framework (React, Vue, etc.)
- UI component library (if any)
- Build and development tooling

#### 3. Project Directory Structure
- Present a clear, production-grade folder organization
- Organize components, assets, and utilities properly

---

### Step 2: Implementation

#### Front-End
- **Strictly replicate prototype visuals**, including:
  - Layout and spacing
  - Typography
  - Color scheme
  - Visual hierarchy
- Implement **all interactions and functionality** as specified in `/workspace/prompt.txt`
- Use actual resource files from `/workspace/resources/`
- Implement proper state management
- Support **all UI states**
- Ensure responsive design if indicated by prototypes
- **In this task, Frontend code may directly include mock data to faithfully reproduce the prototype states and visual presentation**

---

### Step 3: Deployment, Verification, and Script Generation

1. Build and test the application locally to verify:
   - The application is accessible at `http://localhost:3000`
   - All features and interactions work correctly
   - Visuals exactly match the prototype images
2. Generate a **deployment script `/workspace/start.sh`** with the following requirements:
   - The script must be fully self-contained, such that it can be run inside a completely new container with no prior dependencies.
   - Prepare the full environment: install all runtime dependencies, build tools, and required system packages.
   - Start the development server
   - After running:
     ```bash
     bash /workspace/start.sh
     ```
     the application must be fully operational and accessible at  
     **`http://localhost:3000`**, with all features working correctly.
     Note: The application only needs to run in development mode. There is no need to use production configurations.
3. Run start.sh in a clean environment
   - Verify that the script successfully installs dependencies and launches the application.
   - Confirm that the website is fully operational and accessible at http://localhost:3000

---

### Step 4: Documentation

Produce the following documentation:

1. **Design Document**
   - Application architecture
   - Component structure
   - Technology stack
   - Implementation details

2. **README.md**
   - Project overview
   - Technology stack
   - Directory structure
   - Local deployment instructions
   - How to use `start.sh` to start the application
   - Feature list

---

## III. Required Deliverables

Deliverables include:

1. Complete front-end application source code
2. Design document
3. All assets properly organized
4. `/workspace/start.sh` deployment script
5. README with deployment instructions

> Requirement: Place all materials under the /workspace directory.

---

## IV. Hard Constraints

- Do **not** assume unspecified requirements  
- Do **not** skip any required feature from `/workspace/prompt.txt`
- Do **not** merge, remove, or invent features  
- **All features must work correctly and visuals must match the prototypes**  
- **The system must be fully reproducible by running `bash /workspace/start.sh`**

Before final submission, perform a concrete visual self-verification of the application you implemented. Use only the public task requirements, PRD, prototype images, resources, current source code, and observations produced by running your own application.

Follow this procedure after you have obtained the first runnable implementation:

1. Establish the deployed version. Inspect the existing /workspace/start.sh and use it to launch the application at http://localhost:3000. Run it in the background when continued terminal work is needed, wait until the URL responds, and inspect its launch log if startup fails. Do not replace a valid start.sh with an unrelated server command merely for verification.
2. Observe the real application. Open http://localhost:3000 with the browser interface available in the current coding scaffold and inspect the rendered initial state. Make sure actual screenshot pixels enter your context; a screenshot path alone, curl response, raw HTML, HTTP 200, or viewing the provided prototype does not count as observing the implementation.
3. Verify one user-observable requirement at a time. Do not create or execute one whole-site mega-plan. For each check, first state a concise specification with: (a) the functional objective, (b) the expected observable outcome, (c) the reset or starting condition, and (d) a short complete sequence of semantic actions. Actions may include navigate, click, fill, select, hover, press, scroll, or go back. Include only the prerequisites needed for that objective.
4. Execute the complete action sequence using the available browser interface. Inspect the returned step-by-step screenshots, current URL and page state, DOM or accessibility changes, console/runtime errors, and action failures. The browser only executes and records actions; you must decide whether the expected outcome holds.
5. Use evidence before editing. If the observed application conflicts with the requirement or prototype, inspect the responsible code and make the smallest coherent correction. Do not change code solely because you expected a failure, and do not weaken the requirement or expected outcome after seeing the result.
6. Recheck meaningful changes. Ensure the updated code is actually deployed, replay the failed check, and confirm that the observed failure is removed. Also recheck a small, relevant sample of previously successful behavior that the change could affect so that a visual repair does not introduce a functional or visual regression.
7. Continue or stop deliberately. You may perform additional function-scoped checks when they are useful. You decide which requirements to inspect, how many checks to run, whether more editing is necessary, and when the implementation is ready to submit.
````

## Level 3 / `website` / `official`

SHA-256: `dc748cb63d8ba62da48b53c759244c9ca473c7f2d1465f03b2556c96d5c95177`

````text
You are a **senior full-stack engineer with extensive experience in web production development**.

Your responsibility is to **methodically implement and deploy a complete, production-ready web application** strictly in accordance with the provided materials in the current working directory.  
You are required to **follow the instructions continuously until the application is fully operational**.  
**No clarifications or approvals should be sought, and no steps should be omitted prematurely.**

---

## I. Input Materials

The development artifacts provided for this task include:

1. **Product Requirement Document**
   - Location: `/workspace/prd.md`
   - Sections:
     - Product Overview
     - Business Logic
     - Detailed Requirements

2. **Prototype Images**
   - Location: `/workspace/prototypes/*.jpg`
   - Purpose:
     - Define page layout and UI structure
     - Specify visual style, hierarchy, and aesthetics
     - Illustrate interaction behaviors and UI states
     - Serve as the **definitive reference** for visual fidelity

3. **Resource Files**  
   - Location: `/workspace/resources/**/*`  
   - Includes images, videos, audio, icons, and other assets  
   - Must be utilized wherever relevant to faithfully reproduce the prototype content

---

## II. Mandatory Full-Stack Development Workflow

The following workflow must be **strictly adhered to in the specified order**.

### Step 1: Planning and System Design

Produce a **comprehensive design document** encompassing:

#### 1. Data Model and Database Architecture
- Define all tables or collections
- Specify fields, data types, and constraints
- Detail relationships among entities
- Define indexes and rules for data integrity
- Design initial **seed data**, explicitly mapped to prototype content

#### 2. Front-End and Back-End Architecture
- Specify chosen frameworks and libraries
- Define front-end component hierarchy
- Design APIs
- Specify authentication and authorization strategies, if required
- Define input validation and error-handling mechanisms

#### 3. Project Directory Structure
- Present a clear, production-grade folder organization
- Explicitly separate front-end and back-end concerns, where applicable

#### 4. Technology Stack Selection
Explicitly specify the following:
- Front-end framework
- Back-end framework
- Database system
- ORM or data access layer
- UI and styling solution
- Build, development, and runtime tooling

---

### Step 2: Seed Data Generation

- Generate **complete and realistic seed data** for the database.  
- Seed data must:
  - Accurately reflect all visible content depicted in the prototype images
  - Populate all pages, lists, cards, tables, and detail views
  - Support all required UI states (loading, empty, error) as defined in the PRD
  - Fully utilize resource files to replicate the prototypes precisely
- Placeholder data is strictly prohibited

---

### Step 3: Full-Stack Implementation

#### Back-End
- Implement all required APIs
- Enforce:
  - Input validation
  - Error handling
  - Authentication and authorization (if specified)
- Initialize database schema
- Load seed data upon application initialization
- Ensure APIs fully support all front-end use cases

#### Front-End
- **Strictly replicate prototype visuals**, including:
  - Layout and spacing
  - Typography
  - Color scheme
  - Visual hierarchy
- Implement **all interactions** depicted or implied by the prototypes
- Consume live back-end APIs (no hardcoded mock data)
- Support **all UI states**

#### Integration
- Ensure seamless integration between front-end and back-end
- All rendered content must originate from the database
- Seed data must drive the UI precisely as illustrated in the prototypes

---

### Step 4: Deployment, Verification, and Script Generation

1. Initialize the database schema  
2. Load all seed data  
3. Launch the full application and verify:
   - The application is accessible at `http://localhost:3000`
   - All pages and features function correctly
   - Visuals exactly match the prototype images
4. Generate a **deployment script `/workspace/start.sh`** with the following requirements:
   - The script must be fully self-contained, such that it can be run inside a completely new with no prior dependencies or database state.
   - Prepare the full environment: install all runtime dependencies, build tools, and required system packages.
   - Reset the database to a clean initial state, ensuring all previous data or schema remnants are cleared.
   - Apply all database migrations from scratch
   - Load all seed data
   - Deploy both front-end and back-end
   - Start all required services so that the application is fully operational.
   - After running:
     ```bash
     bash /workspace/start.sh
     ```
     the website must be fully operational and accessible at  
     **`http://localhost:3000`**, with a fresh database and fully initialized system.
     Note: The application only needs to run in development mode. There is no need to use production configurations or production environment settings.
5. Run start.sh in a clean environment
   - Verify that the script successfully installs dependencies, initializes the database, loads seed data, and launches the application.
   - Confirm that the website is fully operational and accessible at http://localhost:3000
    
---

### Step 5: Documentation

Produce the following documentation:

1. **Software Design Document**
   - System architecture
   - Data model
   - Technology stack
   - Deployment architecture

2. **README.md**
   - Project overview
   - Technology stack
   - Directory structure
   - Local deployment instructions
   - Database initialization and seed data loading
   - How to use `start.sh` to fully start the system

---

## III. Required Deliverables

Deliverables include:

1. Complete project source code
2. Software design document
3. Seed data
4. `/workspace/start.sh` deployment script
5. README with deployment instructions

> Requirement: Place all materials under the /workspace directory.

---

## IV. Hard Constraints

- Do **not** assume unspecified requirements  
- Do **not** skip any PRD feature  
- Do **not** merge, remove, or invent pages  
- **All pages must visually and functionally match the prototypes**  
- **The system must be fully reproducible by running `bash /workspace/start.sh`**
````

## Level 3 / `website` / `browser_enabled`

SHA-256: `dc748cb63d8ba62da48b53c759244c9ca473c7f2d1465f03b2556c96d5c95177`

````text
You are a **senior full-stack engineer with extensive experience in web production development**.

Your responsibility is to **methodically implement and deploy a complete, production-ready web application** strictly in accordance with the provided materials in the current working directory.  
You are required to **follow the instructions continuously until the application is fully operational**.  
**No clarifications or approvals should be sought, and no steps should be omitted prematurely.**

---

## I. Input Materials

The development artifacts provided for this task include:

1. **Product Requirement Document**
   - Location: `/workspace/prd.md`
   - Sections:
     - Product Overview
     - Business Logic
     - Detailed Requirements

2. **Prototype Images**
   - Location: `/workspace/prototypes/*.jpg`
   - Purpose:
     - Define page layout and UI structure
     - Specify visual style, hierarchy, and aesthetics
     - Illustrate interaction behaviors and UI states
     - Serve as the **definitive reference** for visual fidelity

3. **Resource Files**  
   - Location: `/workspace/resources/**/*`  
   - Includes images, videos, audio, icons, and other assets  
   - Must be utilized wherever relevant to faithfully reproduce the prototype content

---

## II. Mandatory Full-Stack Development Workflow

The following workflow must be **strictly adhered to in the specified order**.

### Step 1: Planning and System Design

Produce a **comprehensive design document** encompassing:

#### 1. Data Model and Database Architecture
- Define all tables or collections
- Specify fields, data types, and constraints
- Detail relationships among entities
- Define indexes and rules for data integrity
- Design initial **seed data**, explicitly mapped to prototype content

#### 2. Front-End and Back-End Architecture
- Specify chosen frameworks and libraries
- Define front-end component hierarchy
- Design APIs
- Specify authentication and authorization strategies, if required
- Define input validation and error-handling mechanisms

#### 3. Project Directory Structure
- Present a clear, production-grade folder organization
- Explicitly separate front-end and back-end concerns, where applicable

#### 4. Technology Stack Selection
Explicitly specify the following:
- Front-end framework
- Back-end framework
- Database system
- ORM or data access layer
- UI and styling solution
- Build, development, and runtime tooling

---

### Step 2: Seed Data Generation

- Generate **complete and realistic seed data** for the database.  
- Seed data must:
  - Accurately reflect all visible content depicted in the prototype images
  - Populate all pages, lists, cards, tables, and detail views
  - Support all required UI states (loading, empty, error) as defined in the PRD
  - Fully utilize resource files to replicate the prototypes precisely
- Placeholder data is strictly prohibited

---

### Step 3: Full-Stack Implementation

#### Back-End
- Implement all required APIs
- Enforce:
  - Input validation
  - Error handling
  - Authentication and authorization (if specified)
- Initialize database schema
- Load seed data upon application initialization
- Ensure APIs fully support all front-end use cases

#### Front-End
- **Strictly replicate prototype visuals**, including:
  - Layout and spacing
  - Typography
  - Color scheme
  - Visual hierarchy
- Implement **all interactions** depicted or implied by the prototypes
- Consume live back-end APIs (no hardcoded mock data)
- Support **all UI states**

#### Integration
- Ensure seamless integration between front-end and back-end
- All rendered content must originate from the database
- Seed data must drive the UI precisely as illustrated in the prototypes

---

### Step 4: Deployment, Verification, and Script Generation

1. Initialize the database schema  
2. Load all seed data  
3. Launch the full application and verify:
   - The application is accessible at `http://localhost:3000`
   - All pages and features function correctly
   - Visuals exactly match the prototype images
4. Generate a **deployment script `/workspace/start.sh`** with the following requirements:
   - The script must be fully self-contained, such that it can be run inside a completely new with no prior dependencies or database state.
   - Prepare the full environment: install all runtime dependencies, build tools, and required system packages.
   - Reset the database to a clean initial state, ensuring all previous data or schema remnants are cleared.
   - Apply all database migrations from scratch
   - Load all seed data
   - Deploy both front-end and back-end
   - Start all required services so that the application is fully operational.
   - After running:
     ```bash
     bash /workspace/start.sh
     ```
     the website must be fully operational and accessible at  
     **`http://localhost:3000`**, with a fresh database and fully initialized system.
     Note: The application only needs to run in development mode. There is no need to use production configurations or production environment settings.
5. Run start.sh in a clean environment
   - Verify that the script successfully installs dependencies, initializes the database, loads seed data, and launches the application.
   - Confirm that the website is fully operational and accessible at http://localhost:3000
    
---

### Step 5: Documentation

Produce the following documentation:

1. **Software Design Document**
   - System architecture
   - Data model
   - Technology stack
   - Deployment architecture

2. **README.md**
   - Project overview
   - Technology stack
   - Directory structure
   - Local deployment instructions
   - Database initialization and seed data loading
   - How to use `start.sh` to fully start the system

---

## III. Required Deliverables

Deliverables include:

1. Complete project source code
2. Software design document
3. Seed data
4. `/workspace/start.sh` deployment script
5. README with deployment instructions

> Requirement: Place all materials under the /workspace directory.

---

## IV. Hard Constraints

- Do **not** assume unspecified requirements  
- Do **not** skip any PRD feature  
- Do **not** merge, remove, or invent pages  
- **All pages must visually and functionally match the prototypes**  
- **The system must be fully reproducible by running `bash /workspace/start.sh`**
````

## Level 3 / `website` / `guided_vsv`

SHA-256: `2e7eb563e1af0e10396ec84882bd1910217cd1e3c919f99d9589e9f75fa1b88b`

````text
You are a **senior full-stack engineer with extensive experience in web production development**.

Your responsibility is to **methodically implement and deploy a complete, production-ready web application** strictly in accordance with the provided materials in the current working directory.  
You are required to **follow the instructions continuously until the application is fully operational**.  
**No clarifications or approvals should be sought, and no steps should be omitted prematurely.**

---

## I. Input Materials

The development artifacts provided for this task include:

1. **Product Requirement Document**
   - Location: `/workspace/prd.md`
   - Sections:
     - Product Overview
     - Business Logic
     - Detailed Requirements

2. **Prototype Images**
   - Location: `/workspace/prototypes/*.jpg`
   - Purpose:
     - Define page layout and UI structure
     - Specify visual style, hierarchy, and aesthetics
     - Illustrate interaction behaviors and UI states
     - Serve as the **definitive reference** for visual fidelity

3. **Resource Files**  
   - Location: `/workspace/resources/**/*`  
   - Includes images, videos, audio, icons, and other assets  
   - Must be utilized wherever relevant to faithfully reproduce the prototype content

---

## II. Mandatory Full-Stack Development Workflow

The following workflow must be **strictly adhered to in the specified order**.

### Step 1: Planning and System Design

Produce a **comprehensive design document** encompassing:

#### 1. Data Model and Database Architecture
- Define all tables or collections
- Specify fields, data types, and constraints
- Detail relationships among entities
- Define indexes and rules for data integrity
- Design initial **seed data**, explicitly mapped to prototype content

#### 2. Front-End and Back-End Architecture
- Specify chosen frameworks and libraries
- Define front-end component hierarchy
- Design APIs
- Specify authentication and authorization strategies, if required
- Define input validation and error-handling mechanisms

#### 3. Project Directory Structure
- Present a clear, production-grade folder organization
- Explicitly separate front-end and back-end concerns, where applicable

#### 4. Technology Stack Selection
Explicitly specify the following:
- Front-end framework
- Back-end framework
- Database system
- ORM or data access layer
- UI and styling solution
- Build, development, and runtime tooling

---

### Step 2: Seed Data Generation

- Generate **complete and realistic seed data** for the database.  
- Seed data must:
  - Accurately reflect all visible content depicted in the prototype images
  - Populate all pages, lists, cards, tables, and detail views
  - Support all required UI states (loading, empty, error) as defined in the PRD
  - Fully utilize resource files to replicate the prototypes precisely
- Placeholder data is strictly prohibited

---

### Step 3: Full-Stack Implementation

#### Back-End
- Implement all required APIs
- Enforce:
  - Input validation
  - Error handling
  - Authentication and authorization (if specified)
- Initialize database schema
- Load seed data upon application initialization
- Ensure APIs fully support all front-end use cases

#### Front-End
- **Strictly replicate prototype visuals**, including:
  - Layout and spacing
  - Typography
  - Color scheme
  - Visual hierarchy
- Implement **all interactions** depicted or implied by the prototypes
- Consume live back-end APIs (no hardcoded mock data)
- Support **all UI states**

#### Integration
- Ensure seamless integration between front-end and back-end
- All rendered content must originate from the database
- Seed data must drive the UI precisely as illustrated in the prototypes

---

### Step 4: Deployment, Verification, and Script Generation

1. Initialize the database schema  
2. Load all seed data  
3. Launch the full application and verify:
   - The application is accessible at `http://localhost:3000`
   - All pages and features function correctly
   - Visuals exactly match the prototype images
4. Generate a **deployment script `/workspace/start.sh`** with the following requirements:
   - The script must be fully self-contained, such that it can be run inside a completely new with no prior dependencies or database state.
   - Prepare the full environment: install all runtime dependencies, build tools, and required system packages.
   - Reset the database to a clean initial state, ensuring all previous data or schema remnants are cleared.
   - Apply all database migrations from scratch
   - Load all seed data
   - Deploy both front-end and back-end
   - Start all required services so that the application is fully operational.
   - After running:
     ```bash
     bash /workspace/start.sh
     ```
     the website must be fully operational and accessible at  
     **`http://localhost:3000`**, with a fresh database and fully initialized system.
     Note: The application only needs to run in development mode. There is no need to use production configurations or production environment settings.
5. Run start.sh in a clean environment
   - Verify that the script successfully installs dependencies, initializes the database, loads seed data, and launches the application.
   - Confirm that the website is fully operational and accessible at http://localhost:3000
    
---

### Step 5: Documentation

Produce the following documentation:

1. **Software Design Document**
   - System architecture
   - Data model
   - Technology stack
   - Deployment architecture

2. **README.md**
   - Project overview
   - Technology stack
   - Directory structure
   - Local deployment instructions
   - Database initialization and seed data loading
   - How to use `start.sh` to fully start the system

---

## III. Required Deliverables

Deliverables include:

1. Complete project source code
2. Software design document
3. Seed data
4. `/workspace/start.sh` deployment script
5. README with deployment instructions

> Requirement: Place all materials under the /workspace directory.

---

## IV. Hard Constraints

- Do **not** assume unspecified requirements  
- Do **not** skip any PRD feature  
- Do **not** merge, remove, or invent pages  
- **All pages must visually and functionally match the prototypes**  
- **The system must be fully reproducible by running `bash /workspace/start.sh`**

Before final submission, perform a concrete visual self-verification of the application you implemented. Use only the public task requirements, PRD, prototype images, resources, current source code, and observations produced by running your own application.

Follow this procedure after you have obtained the first runnable implementation:

1. Establish the deployed version. Inspect the existing /workspace/start.sh and use it to launch the application at http://localhost:3000. Run it in the background when continued terminal work is needed, wait until the URL responds, and inspect its launch log if startup fails. Do not replace a valid start.sh with an unrelated server command merely for verification.
2. Observe the real application. Open http://localhost:3000 with the browser interface available in the current coding scaffold and inspect the rendered initial state. Make sure actual screenshot pixels enter your context; a screenshot path alone, curl response, raw HTML, HTTP 200, or viewing the provided prototype does not count as observing the implementation.
3. Verify one user-observable requirement at a time. Do not create or execute one whole-site mega-plan. For each check, first state a concise specification with: (a) the functional objective, (b) the expected observable outcome, (c) the reset or starting condition, and (d) a short complete sequence of semantic actions. Actions may include navigate, click, fill, select, hover, press, scroll, or go back. Include only the prerequisites needed for that objective.
4. Execute the complete action sequence using the available browser interface. Inspect the returned step-by-step screenshots, current URL and page state, DOM or accessibility changes, console/runtime errors, and action failures. The browser only executes and records actions; you must decide whether the expected outcome holds.
5. Use evidence before editing. If the observed application conflicts with the requirement or prototype, inspect the responsible code and make the smallest coherent correction. Do not change code solely because you expected a failure, and do not weaken the requirement or expected outcome after seeing the result.
6. Recheck meaningful changes. Ensure the updated code is actually deployed, replay the failed check, and confirm that the observed failure is removed. Also recheck a small, relevant sample of previously successful behavior that the change could affect so that a visual repair does not introduce a functional or visual regression.
7. Continue or stop deliberately. You may perform additional function-scoped checks when they are useful. You decide which requirements to inspect, how many checks to run, whether more editing is necessary, and when the implementation is ready to submit.
````

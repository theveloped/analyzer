def visualize_milling_tool(tool_diameter, nose_radius, flute_length, shank_diameter, usable_length, holder_diameter, holder_length):
    # Terminal width (characters wide)
    max_width = 80
    
    # Calculate total length and determine scaling factor
    total_length = flute_length + holder_length
    scale = max_width / total_length

    # Define widths for different sections
    flute_width = int(flute_length * scale)
    usable_width = int(usable_length * scale)
    holder_width = int(holder_length * scale)

    # Tool depiction variables
    max_diameter = max(tool_diameter, shank_diameter, holder_diameter)
    height = max_diameter  # height of the tool in characters

    # Generate lines for each section
    def generate_section(width, diameter):
        radius = diameter // 2
        section = []
        for i in range(-radius, radius + 1):
            if abs(i) == radius:  # Top or bottom
                line = ('+' + '-' * (diameter - 2) + '+').center(max_diameter)
            else:  # Middle parts
                line = ('|' + ' ' * (diameter - 2) + '|').center(max_diameter)
            section.append(line)
        # Adjust height uniformly across all sections
        while len(section) < height:
            section.insert(0, ' ' * max_diameter)
            section.append(' ' * max_diameter)
        return section[:height]

    # Create each section of the tool
    flute_section = generate_section(flute_width, tool_diameter)
    shank_section = generate_section(usable_width - flute_width, shank_diameter)
    holder_section = generate_section(holder_width, holder_diameter)

    # Combine sections vertically
    tool_representation = []
    for i in range(height):
        line = flute_section[i] + shank_section[i] + holder_section[i]
        tool_representation.append(line)

    # Print each line of the tool
    for line in tool_representation:
        print(line)
    
    # Print dimensions below the tool
    dimension_line = f"{flute_length}cm{' ' * (flute_width - len(f'{flute_length}cm'))}" \
                     f"{usable_length - flute_length}cm{' ' * ((usable_width - flute_width) - len(f'{usable_length - flute_length}cm'))}" \
                     f"{holder_length}cm"
    print(dimension_line.center(max_width))

visualize_milling_tool(
    tool_diameter=10,
    nose_radius=1,
    flute_length=20,
    shank_diameter=5,
    usable_length=35,
    holder_diameter=4,
    holder_length=15
)
function solveLinearSystem(matrix, values) {
  const size = values.length;
  const augmented = matrix.map((row, index) => [...row, values[index]]);
  for (let column = 0; column < size; column += 1) {
    let pivot = column;
    for (let row = column + 1; row < size; row += 1) {
      if (Math.abs(augmented[row][column]) > Math.abs(augmented[pivot][column])) pivot = row;
    }
    if (Math.abs(augmented[pivot][column]) < 1e-10) throw new Error("Calibration points are too close together or incorrectly ordered.");
    [augmented[column], augmented[pivot]] = [augmented[pivot], augmented[column]];
    const scale = augmented[column][column];
    for (let index = column; index <= size; index += 1) augmented[column][index] /= scale;
    for (let row = 0; row < size; row += 1) {
      if (row === column) continue;
      const factor = augmented[row][column];
      for (let index = column; index <= size; index += 1) augmented[row][index] -= factor * augmented[column][index];
    }
  }
  return augmented.map((row) => row[size]);
}

export function solvePlanarHomography(imagePoints, worldPoints) {
  if (imagePoints.length !== 4 || worldPoints.length !== 4) throw new Error("Select all four work-area corners before calibrating.");
  const rows = [];
  const values = [];
  imagePoints.forEach(([u, v], index) => {
    const [x, y] = worldPoints[index];
    rows.push([u, v, 1, 0, 0, 0, -x * u, -x * v]);
    values.push(x);
    rows.push([0, 0, 0, u, v, 1, -y * u, -y * v]);
    values.push(y);
  });
  return solveLinearSystem(rows, values);
}

export function imagePointToWorkcell(point, homography) {
  const [u, v] = point;
  const denominator = homography[6] * u + homography[7] * v + 1;
  if (Math.abs(denominator) < 1e-10) throw new Error("The image point is outside the calibrated work surface.");
  return {
    x: (homography[0] * u + homography[1] * v + homography[2]) / denominator,
    y: (homography[3] * u + homography[4] * v + homography[5]) / denominator,
  };
}
